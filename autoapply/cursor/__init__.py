"""One controller per page. Application helpers deliberately synchronize only once."""
from .controller import CursorController
from .types import CursorConfig, CursorState, Point, CursorError, CursorCancelledError


def get_cursor(page, config=None, security_guard=None):
    cursor = getattr(page, "_autoapply_cursor", None)
    if cursor is None:
        if security_guard is None:
            async def security_guard():
                from ..security import SecurityDetector
                result, _ = await SecurityDetector().detect(page)
                return result.blocking
        cursor = CursorController(page, config, security_guard=security_guard)
        page._autoapply_cursor = cursor
    return cursor


async def ready_cursor(page):
    cursor = get_cursor(page)
    # A deliberate first synchronization, never reused after cancellation/manual input.
    if not getattr(page, "_autoapply_cursor_initialized", False):
        page._autoapply_cursor_initialized = True
        if cursor.state != CursorState.IDLE:
            raise CursorError("Cursor requires explicit recovery")
        await cursor.synchronize(Point(1, 1))
    return cursor


async def click_element(page, locator, **options):
    # A cookie banner backdrop otherwise fails the hit test for every target.
    from ..consent import dismiss_consent, dismiss_overlay
    if not (getattr(page, '_autoapply_consent_busy', False) or
            getattr(page, '_autoapply_overlay_busy', False)):
        await dismiss_consent(page, click_element)
        await dismiss_overlay(page, click_element)
    if getattr(page, '_autoapply_entry_only', False):
        # Entry probes cannot deliver form submits, cookie acceptance, or any
        # action outside this explicit navigation/dismissal vocabulary.
        allowed_script = r'''e => {
          const name=(e.getAttribute('aria-label')||
            (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ').trim()||
            e.innerText||e.value||'').replace(/\s+/g,' ').trim();
          return !e.matches('input[type="submit"],input[type="image"],button[type="submit"]') && !(e.form&&e.type==='submit') &&
            !/^accept\b/i.test(name) && !/^accept\b/i.test((e.innerText||e.value||'').trim()) &&
            (/^(apply|apply now|apply for this job|apply to this job|apply manually|application)$/i.test(name) ||
             e.hasAttribute('data-autoapply-consent') || e.hasAttribute('data-autoapply-overlay-close'));
        }'''
        if not await locator.evaluate(allowed_script):
            raise CursorError('Entry-only probe rejected a non-entry control')
        step = await locator.evaluate('''e => e.hasAttribute('data-autoapply-consent') ? 'cookie_declined' :
          e.hasAttribute('data-autoapply-overlay-close') ? 'overlay_dismissed' :
          'entry_clicked:'+(e.getAttribute('aria-label')||e.innerText||e.value||'').replace(/\\s+/g,' ').trim()''')
    cursor = await ready_cursor(page)
    if getattr(page, '_autoapply_entry_only', False):
        original_guard = cursor.security_guard

        async def entry_guard():
            if original_guard and await original_guard():
                return True
            # Preserve existing post-delivery transition/security checks. Before
            # press, recheck the vocabulary and type against current markup.
            if cursor.state not in {CursorState.PRESSING, CursorState.WAITING_FOR_RESULT}:
                if not await locator.evaluate(allowed_script):
                    raise CursorError('Entry-only target became a non-entry control')
            return False

        cursor.security_guard = entry_guard
        try:
            await cursor.click_element(locator, **options)
        finally:
            cursor.security_guard = original_guard
    else:
        await cursor.click_element(locator, **options)
    if getattr(page, '_autoapply_entry_only', False):
        page._autoapply_entry_steps.append(step)


async def native_control(page, operation, reason):
    return await (await ready_cursor(page)).native_control(operation, reason)


__all__ = ["CursorController", "CursorConfig", "CursorState", "Point", "CursorError", "CursorCancelledError",
           "get_cursor", "click_element", "native_control"]
