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
    await (await ready_cursor(page)).click_element(locator, **options)


async def native_control(page, operation, reason):
    return await (await ready_cursor(page)).native_control(operation, reason)


__all__ = ["CursorController", "CursorConfig", "CursorState", "Point", "CursorError", "CursorCancelledError",
           "get_cursor", "click_element", "native_control"]
