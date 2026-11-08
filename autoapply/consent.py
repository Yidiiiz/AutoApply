"""Decline cookie-consent banners whose backdrop blocks application entry clicks."""
import asyncio
import logging
from .form_controls import DOM_JS, CONTROL_SELECTOR, application_frame

log = logging.getLogger('autoapply')

MARK = 'data-autoapply-consent'

# Returns the number of visible decline/reject controls inside a cookie banner,
# tagging the best one. Accept is never chosen; no banner means no action.
FIND = r"""(mark) => {
  document.querySelectorAll('['+mark+']').forEach(e => e.removeAttribute(mark));
  const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none' && !e.disabled; };
  const label = e => (e.getAttribute('aria-label')||e.innerText||e.value||'').replace(/\s+/g,' ').trim();
  const decline = /^(decline|decline all|decline cookies|decline optional cookies|reject|reject all|reject all cookies|reject cookies|deny|deny all|refuse|refuse all|only necessary|necessary only|only necessary cookies|use necessary cookies only|accept only necessary|accept necessary only|essential only|only essential cookies|continue without accepting)$/i;
  const known = document.querySelector('#onetrust-reject-all-handler, #CybotCookiebotDialogBodyButtonDecline, [data-automation-id="legalNoticeDeclineButton"]');
  if (known && visible(known)) { known.setAttribute(mark,'1'); return 1; }
  const found = [];
  for (const e of document.querySelectorAll('button, [role=button], a, input[type=button]')) {
    if (!visible(e) || !decline.test(label(e))) continue;
    let n=e, scope=false;
    for (let i=0; n && i<8; i++, n=n.parentElement) {
      if (/cookie|consent|privacy/i.test((n.id||'')+' '+(typeof n.className==='string'?n.className:'')+' '+(n.getAttribute('aria-label')||''))
          || (n.innerText||'').length<1500 && /cookie/i.test(n.innerText||'')) { scope=true; break; }
    }
    if (scope) found.push(e);
  }
  if (found.length) found[0].setAttribute(mark,'1');
  return found.length;
}"""


async def dismiss_consent(page, click):
    """Click one decline control with the supplied guarded click; never accept."""
    if getattr(page, '_autoapply_consent_busy', False):
        return False
    try:
        if not await page.evaluate(FIND, MARK):
            return False
    except Exception:
        return False
    target = page.locator(f'[{MARK}="1"]')
    page._autoapply_consent_busy = True
    try:
        try:
            await click(page, target)
        except Exception as exc:
            log.info('[CONSENT] Cookie banner decline failed: %s', type(exc).__name__)
            return False
        for _ in range(20):
            if not await target.count() or not await target.first.is_visible():
                break
            await asyncio.sleep(.15)
        log.info('[CONSENT] Declined cookie banner url=%s', page.url.split('?')[0])
        return True
    finally:
        page._autoapply_consent_busy = False


OVERLAY_MARK = 'data-autoapply-overlay-close'
FIND_OVERLAY = r"""(mark) => {
  __DOM__
  deepQuery(document,'['+mark+']').forEach(e => e.removeAttribute(mark));
  const close = /^(close|close (?:dialog|popup|chat)|x|\u00d7|no thanks|not now|maybe later|dismiss)$/i;
  const label = e => (e.getAttribute('aria-label')||e.innerText||e.value||'').replace(/\s+/g,' ').trim();
  for (const dialog of deepQuery(document,'[role="dialog"],[aria-modal="true"],[class*="chat-widget"],[id*="chat-widget"],[data-chat-widget],[class*="newsletter-modal"],[class*="talent-community"]')) {
    if (!visible(dialog) || deepQuery(dialog,'__CONTROLS__,iframe').length) continue;
    // Consent has its own decline-only policy. Never dismiss application choosers.
    if (/cookie|consent|start your application|apply manually|autofill with resume|use my last application|captcha|verif(?:y|ication)|security|sign in|log in|create (?:an? )?account/i.test(dialog.innerText||'')) continue;
    const controls = deepQuery(dialog,'button,a,[role="button"],[role="link"]').filter(visible);
    const clutter = /newsletter|talent community|chat|message us/i.test((dialog.innerText||'')+' '+dialog.className+' '+dialog.id) || dialog.hasAttribute('data-chat-widget');
    if (!controls.length || !clutter && controls.some(e => !close.test(label(e)))) continue;
    const safe = controls.filter(e => close.test(label(e)) && !/^accept|submit|send|apply/i.test(label(e)) &&
      !e.matches('input[type="submit"],button[type="submit"]') && !(e.form && e.type==='submit'));
    if (!safe.length) continue;
    safe[0].setAttribute(mark,'1');
    return true;
  }
  return false;
}""".replace('__DOM__', DOM_JS).replace('__CONTROLS__', CONTROL_SELECTOR)


async def dismiss_overlay(page, click):
    """Close one input-free clutter container through the guarded cursor."""
    if getattr(page, '_autoapply_overlay_busy', False):
        return False
    try:
        frame = None
        for candidate in page.frames:
            if application_frame(page, candidate) and await candidate.evaluate(FIND_OVERLAY, OVERLAY_MARK):
                frame = candidate
                break
        if frame is None:
            return False
    except Exception:
        return False
    page._autoapply_overlay_busy = True
    try:
        target = frame.locator(f'[{OVERLAY_MARK}="1"]')
        try:
            await click(page, target)
        except Exception as exc:
            log.info('[OVERLAY] Close failed: %s', type(exc).__name__)
            return False
        for _ in range(20):
            if not await target.count() or not await target.first.is_visible():
                break
            await asyncio.sleep(.15)
        return True
    finally:
        page._autoapply_overlay_busy = False


async def pass_sticky_header(target):
    """Scroll an occluded target below a navigation header; never hide page DOM."""
    await target.evaluate(r'''e => {
      const d=e.ownerDocument, w=d.defaultView, r=e.getBoundingClientRect();
      if(r.width<=0 || r.height<=0) return;
      const x=r.left+r.width/2,y=r.top+r.height/2;
      if(x<0 || x>=w.innerWidth || y<0 || y>=w.innerHeight) return;
      const hit=d.elementFromPoint(x,y);
      if(!hit || hit===e || e.contains(hit) || hit.contains(e)) return;
      const header=hit.closest('header,nav,[role="banner"],[role="navigation"]');
      if(!header || header.contains(e) || header.querySelector('input,textarea,select,[contenteditable="true"]')) return;
      if(!['fixed','sticky'].includes(w.getComputedStyle(header).position)) return;
      const h=header.getBoundingClientRect();
      if(h.top<=1 && h.bottom<w.innerHeight/2)
        d.scrollingElement?.scrollBy({top:r.top-h.bottom-16,behavior:'instant'});
    }''')
