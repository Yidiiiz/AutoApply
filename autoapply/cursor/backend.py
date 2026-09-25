"""Real browser input backends, with conservative ownership on uncertain failures."""
import asyncio
from typing import Protocol
from .types import MouseInputState, CursorError

BUTTON_MASK = {"left": 1, "right": 2, "middle": 4}
MODIFIER_MASK = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}


class MouseInputBackend(Protocol):
    async def move(self, point, state): ...
    async def down(self, point, button, state): ...
    async def up(self, point, button, state): ...
    async def key_down(self, key): ...
    async def key_up(self, key): ...


class PlaywrightMouseBackend:
    def __init__(self, page):
        self.page = page

    async def move(self, point, state):
        await self.page.mouse.move(point.x, point.y)

    async def down(self, point, button, state):
        await self.page.mouse.down(button=button, click_count=state.click_count)

    async def up(self, point, button, state):
        await self.page.mouse.up(button=button, click_count=state.click_count)

    async def key_down(self, key):
        await self.page.keyboard.down(key)

    async def key_up(self, key):
        await self.page.keyboard.up(key)


class CDPMouseBackend(PlaywrightMouseBackend):
    def __init__(self, page):
        super().__init__(page)
        self.session = None

    async def dispatch(self, event, point, state, button="none"):
        if self.session is None:
            self.session = await self.page.context.new_cdp_session(self.page)
        await self.session.send("Input.dispatchMouseEvent", {
            "type": event, "x": point.x, "y": point.y, "button": button,
            "buttons": sum(BUTTON_MASK[b] for b in state.buttons),
            "modifiers": sum(MODIFIER_MASK[m] for m in state.modifiers),
            "clickCount": state.click_count if event != "mouseMoved" else 0,
            "pointerType": state.pointer_type})

    async def move(self, point, state):
        await self.dispatch("mouseMoved", point, state, next(iter(sorted(state.buttons)), "none"))

    async def down(self, point, button, state):
        await self.dispatch("mousePressed", point, state, button)

    async def up(self, point, button, state):
        await self.dispatch("mouseReleased", point, state, button)


class InputStateManager:
    def __init__(self, backend, timeout_ms=3000):
        self.backend, self.state = backend, MouseInputState()
        self.timeout = timeout_ms/1000

    async def _dispatch(self, operation):
        return await asyncio.wait_for(operation, self.timeout)

    async def modifiers(self, keys):
        for key in keys:
            if key not in MODIFIER_MASK:
                raise ValueError("Unsupported cursor modifier")
            if key not in self.state.modifiers:
                self.state.modifiers.add(key)  # Own before dispatch: failure may be ambiguous.
                await self._dispatch(self.backend.key_down(key))

    async def down(self, point, button, count=1):
        if button not in BUTTON_MASK or count not in {1, 2} or button in self.state.buttons:
            raise CursorError("Invalid or already-owned cursor button")
        self.state.click_count = count
        self.state.buttons.add(button)
        await self._dispatch(self.backend.down(point, button, self.state))

    async def up(self, point, button):
        if button not in self.state.buttons:
            raise CursorError("Cannot release an unowned button")
        self.state.buttons.remove(button)
        try:
            await self._dispatch(self.backend.up(point, button, self.state))
        except BaseException:
            self.state.buttons.add(button)
            raise

    async def reset(self, point):
        failures = []
        for button in sorted(self.state.buttons.copy()):
            try:
                await self.up(point, button)
            except Exception as exc:
                failures.append(exc)
        for key in sorted(self.state.modifiers.copy()):
            try:
                await self._dispatch(self.backend.key_up(key))
                self.state.modifiers.remove(key)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise CursorError("Owned input cleanup failed; automation must remain stopped") from failures[0]
