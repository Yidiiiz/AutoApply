"""Serialized movement and physical input, fail-closed at every press boundary."""
import asyncio
from contextlib import asynccontextmanager
import logging
import random
import time
import uuid

from .backend import CDPMouseBackend, PlaywrightMouseBackend, InputStateManager, BUTTON_MASK, MODIFIER_MASK
from .planning import BezierPlanner, bound
from .target import TargetResolver, box_difference
from .types import CursorConfig, CursorState as S, Point, CursorError, CursorCancelledError, TargetUnstableError

log = logging.getLogger("autoapply.cursor")

TRANSITIONS = {
    S.IDLE: {S.RESOLVING, S.MOVING, S.PRESSING},
    S.RESOLVING: {S.ACTIONABILITY_CHECK},
    S.ACTIONABILITY_CHECK: {S.SCROLLING},
    S.SCROLLING: {S.STABILIZING},
    S.STABILIZING: {S.MOVING},
    S.MOVING: {S.REVALIDATING, S.IDLE, S.RESOLVING},
    S.REVALIDATING: {S.PRESSING, S.RESOLVING, S.IDLE},
    S.PRESSING: {S.DRAGGING, S.WAITING_FOR_RESULT, S.IDLE},
    S.DRAGGING: {S.WAITING_FOR_RESULT},
    S.WAITING_FOR_RESULT: {S.IDLE, S.REVALIDATING},
    S.MANUAL_REQUIRED: {S.IDLE}, S.CANCELLED: {S.IDLE},
}

GEOMETRY_SCRIPT = """() => {
  if (!window.__autoapplyCursorGeometry) {
    const g=window.__autoapplyCursorGeometry={count:0};
    const bump=()=>g.count++;
    addEventListener('scroll',bump,true); addEventListener('resize',bump);
    addEventListener('orientationchange',bump);
    visualViewport?.addEventListener('resize',bump);
    visualViewport?.addEventListener('scroll',bump);
    new MutationObserver(bump).observe(document,{subtree:true,childList:true,attributes:true});
    new ResizeObserver(bump).observe(document.documentElement);
    try { new PerformanceObserver(bump).observe({type:'layout-shift',buffered:false}); } catch {}
  }
  return [window.__autoapplyCursorGeometry.count,innerWidth,innerHeight,
          visualViewport?.scale,visualViewport?.offsetLeft,visualViewport?.offsetTop];
}"""


class CursorController:
    def __init__(self, page, config=None, *, backend=None, rng=None, security_guard=None):
        self.page, self.config = page, config or CursorConfig()
        self.enabled = self.config.enabled
        self.current_position = None
        self.position_known = False
        self.current_target = None
        self.state = S.IDLE
        self.generation_id = self.geometry_generation = 0
        self._geometry_snapshot = None
        self._lock = asyncio.Lock()
        self._held_owner = None
        self._last_input_position = None
        self._closed = False
        self.rng = rng if rng is not None else random.Random()
        self.backend = backend or (CDPMouseBackend(page) if self.config.backend == "cdp" else PlaywrightMouseBackend(page))
        self.inputs = InputStateManager(self.backend, self.config.actionability_timeout_ms)
        self.input_state = self.inputs.state
        self.resolver = TargetResolver(self.config, self.rng)
        self.planner = BezierPlanner(self.config, self.rng)
        if security_guard is None:
            async def security_guard():
                from ..security import SecurityDetector
                result, _ = await SecurityDetector().detect(page)
                return result.blocking
        self.security_guard = security_guard
        self.events = []  # bounded, value-free diagnostics; never locator text/URLs/form data
        self._interaction_id = None
        page.on("framenavigated", self._navigation)
        page.on("framedetached", self._navigation)
        page.on("close", self._close)
        page.on("crash", self._close)

    def _event(self, event, **data):
        record = dict(interaction_id=self._interaction_id, event=event, state=self.state.value,
                      generation=self.generation_id, geometry_generation=self.geometry_generation,
                      backend=self.config.backend, **data)
        self.events.append(record)
        self.events = self.events[-200:]
        log.debug("Cursor %s", record)

    def _set(self, state):
        if state == self.state:
            return
        if state not in {S.CANCELLED, S.MANUAL_REQUIRED} and state not in TRANSITIONS[self.state]:
            raise CursorError(f"Invalid cursor transition {self.state} -> {state}")
        self.state = state

    def _navigation(self, frame):
        self.geometry_generation += 1
        held_between_operations = self.state == S.IDLE and bool(self.input_state.buttons)
        if held_between_operations or self.state not in {S.IDLE, S.MANUAL_REQUIRED, S.CANCELLED, S.WAITING_FOR_RESULT}:
            self._invalidate(False, "navigation")
        if held_between_operations:
            asyncio.create_task(self._navigation_cleanup(self.generation_id))

    async def _navigation_cleanup(self, generation):
        async with self._lock:
            if generation != self.generation_id:
                return  # Explicit recovery already cleaned this generation's input.
            try:
                await self._cleanup()
            except Exception:
                self._event("cleanup_failed", reason="navigation")

    def _close(self, *_):
        self._closed = True
        self._invalidate(False)
        self.current_position, self.position_known = None, False
        # The destroyed browsing context no longer owns physical input.
        self.input_state.buttons.clear()
        self.input_state.modifiers.clear()

    def _invalidate(self, manual, reason="explicit stop"):
        self.generation_id += 1
        self.current_target = None
        self._set(S.MANUAL_REQUIRED if manual or self.state == S.MANUAL_REQUIRED else S.CANCELLED)
        self._event("manual_required" if manual else "cancelled", reason=reason)

    def _check(self, generation):
        if self._closed or self.page.is_closed() or generation != self.generation_id or self.state in {S.MANUAL_REQUIRED, S.CANCELLED}:
            raise CursorCancelledError("Cursor interaction invalidated")
        self.config.authorize(self.page.url)

    async def _guard(self, generation):
        self._check(generation)
        if await self._security_blocked():
            self._invalidate(True, "security hold")
            raise CursorCancelledError("Security hold stopped cursor interaction")
        self._check(generation)

    async def _security_blocked(self):
        return self.security_guard and await asyncio.wait_for(self.security_guard(),
                                                             self.config.actionability_timeout_ms/1000)

    async def _geometry(self):
        snapshot = []
        for frame in self.page.frames:
            snapshot.append((id(frame), await asyncio.wait_for(frame.evaluate(GEOMETRY_SCRIPT),
                            self.config.actionability_timeout_ms/1000)))
        if snapshot != self._geometry_snapshot:
            self.geometry_generation += 1
            self._geometry_snapshot = snapshot
        return self.geometry_generation

    async def _viewport(self):
        return tuple(await asyncio.wait_for(self.page.evaluate("() => [innerWidth,innerHeight]"),
                                           self.config.actionability_timeout_ms/1000))

    async def _cleanup(self):
        position = self.current_position if self.position_known else self._last_input_position
        if not self._closed and position is not None:
            await self.inputs.reset(position)
        self._held_owner = None

    @asynccontextmanager
    async def _operation(self, *, held=False):
        generation = self.generation_id  # capture BEFORE waiting: cancel also invalidates queued work
        self._check(generation)
        async with self._lock:
            self._check(generation)
            if not self.enabled or not self.position_known:
                raise CursorError("Explicit cursor synchronization required")
            if self.input_state.buttons and (not held or self._held_owner is not asyncio.current_task()):
                raise CursorError("Another interaction owns held input")
            self._interaction_id = uuid.uuid4().hex
            self._event("start", position=vars(self.current_position), position_known=self.position_known,
                        target="logical locator" if self.current_target else None)
            try:
                await self._guard(generation)
                yield generation
            except BaseException as exc:
                self._event("aborted", reason=type(exc).__name__)
                if self.state != S.MANUAL_REQUIRED:
                    self._set(S.CANCELLED)
                self.generation_id += 1
                try:
                    await asyncio.shield(self._cleanup())
                finally:
                    if self.state == S.MANUAL_REQUIRED:
                        self.current_position, self.position_known = None, False
                from ..scrolling import NavigationError
                if isinstance(exc, (CursorError, NavigationError, asyncio.CancelledError)):
                    raise
                raise CursorError("Browser cursor interaction failed; explicit recovery required") from exc
            finally:
                self.current_target = None
                self._event("end")

    async def _resolve(self, locator, generation):
        self._set(S.RESOLVING)
        self.current_target = locator
        self._set(S.ACTIONABILITY_CHECK)
        await asyncio.wait_for(self.resolver.actionability.check(locator, self.config.actionability_timeout_ms),
                               self.config.actionability_timeout_ms/1000)
        self._check(generation)
        self._set(S.SCROLLING)
        await self.resolver.scroll(locator, lambda: self._check(generation), guard=lambda: self._guard(generation))
        self._set(S.STABILIZING)
        await self._geometry()  # Install observers before the settling interval.
        box = await self.resolver.geometry.stable(locator, lambda: self._check(generation))
        await asyncio.wait_for(self.resolver.actionability.check(locator, self.config.actionability_timeout_ms),
                               self.config.actionability_timeout_ms/1000)
        geometry = await self._geometry()
        target = await asyncio.wait_for(self.resolver.select(locator, box, geometry, await self._viewport()),
                                        self.config.actionability_timeout_ms/1000)
        self._check(generation)
        self._event("resolved", box=box, point=vars(target.point), strategy=self.config.target_strategy)
        return target

    async def _valid(self, target, generation, *, arrival):
        await self._guard(generation)
        await asyncio.wait_for(self.resolver.actionability.check(target.locator, self.config.actionability_timeout_ms),
                               self.config.actionability_timeout_ms/1000)
        box = await target.locator.bounding_box(timeout=self.config.actionability_timeout_ms)
        geometry = await self._geometry()
        valid = bool(box and box_difference(box, target.box) <= self.config.target_move_tolerance_px)
        valid = valid and geometry == target.geometry_generation
        if valid and arrival:
            valid = await asyncio.wait_for(self.resolver.hit_test.valid(target.locator, self.current_position),
                                           self.config.actionability_timeout_ms/1000)
        self._check(generation)
        self._event("revalidated", valid=valid, box=box, hit_test=bool(valid) if arrival else None)
        return valid

    async def _move(self, end, generation, target=None, dragging=False):
        if self.current_position is None or not self.position_known:
            raise CursorError("Movement requires a synchronized position")
        viewport = await self._viewport()
        path = self.planner.plan(self.current_position, end, viewport)
        self._event("path", start=vars(self.current_position), end=vars(end), samples=len(path),
                    distance=self.current_position.distance(end), duration_ms=path[-1].time_ms,
                    easing=self.config.easing, noise=self.config.noise_enabled, overshoot=self.config.overshoot_enabled)
        started, last_check = time.monotonic(), time.monotonic()
        for point in path:
            await asyncio.sleep(max(0, started+point.time_ms/1000-time.monotonic()))
            self._check(generation)
            # Never dispatch a coordinate from an obsolete viewport. Element moves
            # re-resolve; point moves/drags fail closed because their destination
            # cannot be safely inferred in the new coordinate system.
            if target and not dragging:
                geometry = await self._geometry()
                current_viewport = tuple(self._geometry_snapshot[0][1][1:3])
            else:
                geometry = None
                current_viewport = await self._viewport()
            if current_viewport != viewport:
                if target and not dragging:
                    return False
                raise TargetUnstableError("Viewport changed during movement")
            if target and not dragging and geometry != target.geometry_generation:
                return False
            if (time.monotonic()-last_check)*1000 >= self.config.revalidation_interval_ms:
                await self._guard(generation)
                if target and path[-1].time_ms >= self.config.midflight_threshold_ms and not dragging:
                    if not await self._valid(target, generation, arrival=False):
                        return False
                last_check = time.monotonic()
            self._check(generation)
            self.position_known = False  # Dispatch failure leaves physical position uncertain.
            self._last_input_position = Point(point.x, point.y)
            await asyncio.wait_for(self.backend.move(point, self.input_state), self.config.actionability_timeout_ms/1000)
            self.current_position = Point(point.x, point.y)
            self.position_known = True
            self._check(generation)
        return True

    async def _arrive(self, locator, generation):
        for attempt in range(self.config.max_replans+1):
            target = await self._resolve(locator, generation)
            self._set(S.MOVING)
            reached = await self._move(target.point, generation, target)
            self._set(S.REVALIDATING)
            if reached and await self._valid(target, generation, arrival=True):
                return target
            self._event("replan", attempt=attempt+1)
        raise TargetUnstableError("Cursor target changed during movement")

    async def move_to_element(self, locator):
        async with self._operation() as generation:
            target = await self._arrive(locator, generation)
            self._set(S.IDLE)
            return target

    async def move_to_point(self, point):
        async with self._operation(held=True) as generation:
            self._set(S.MOVING)
            await self._move(point, generation)
            self._set(S.IDLE)

    @staticmethod
    def _options(button, click_count, modifiers, press_duration_ms):
        if button not in BUTTON_MASK or click_count not in {1, 2} or any(m not in MODIFIER_MASK for m in modifiers):
            raise ValueError("Invalid cursor press options")
        if press_duration_ms is not None and not 0 <= press_duration_ms <= 1000:
            raise ValueError("Press duration must be between 0 and 1000ms")

    async def click_element(self, locator, *, button="left", click_count=1, modifiers=(), press_duration_ms=None):
        if not self.config.perform_physical_clicks:
            raise CursorError("Physical presses are disabled")
        self._options(button, click_count, modifiers, press_duration_ms)
        async with self._operation() as generation:
            target = await self._arrive(locator, generation)
            await self.inputs.modifiers(modifiers)
            for count in range(1, click_count+1):
                # Modifiers can change geometry; check again before each physical down.
                if not await self._valid(target, generation, arrival=True):
                    raise TargetUnstableError("Target changed at press boundary")
                self._set(S.PRESSING)
                self._check(generation)
                await self.inputs.down(self.current_position, button, count)
                duration = self.planner.timing.press_duration() if press_duration_ms is None else press_duration_ms
                await asyncio.sleep(duration/1000)
                await self._guard(generation)
                self._set(S.WAITING_FOR_RESULT)
                self._check(generation)
                await self.inputs.up(self.current_position, button)
                self._event("released", button=button, click_count=count, modifiers=list(modifiers))
                if count < click_count:
                    self._set(S.REVALIDATING)
            await self._cleanup()
            # Inspect state only; submission confirmation belongs to the existing classifier.
            self._event("result", closed=self.page.is_closed())
            if not self._closed:
                self._set(S.IDLE)

    async def drag(self, source, destination, *, button="left", modifiers=()):
        if not self.config.perform_physical_clicks:
            raise CursorError("Physical presses are disabled")
        self._options(button, 1, modifiers, None)
        async with self._operation() as generation:
            target = await self._arrive(source, generation)
            # Destination is a viewport point; do not scroll or hit-test source during capture.
            if bound(destination, await self._viewport()) != destination:
                raise CursorError("Drag destination outside viewport")
            await self.inputs.modifiers(modifiers)
            if not await self._valid(target, generation, arrival=True):
                raise TargetUnstableError("Drag source changed at press boundary")
            self._set(S.PRESSING)
            await self.inputs.down(self.current_position, button)
            self._set(S.DRAGGING)
            await self._move(destination, generation, dragging=True)
            await self._guard(generation)
            self._set(S.WAITING_FOR_RESULT)
            await self.inputs.up(self.current_position, button)
            await self._cleanup()
            self._set(S.IDLE)

    async def press(self, *, button="left", modifiers=()):
        if not self.config.perform_physical_clicks:
            raise CursorError("Physical presses are disabled")
        self._options(button, 1, modifiers, None)
        async with self._operation() as generation:
            await self.inputs.modifiers(modifiers)
            await self._guard(generation)
            self._set(S.PRESSING)
            await self.inputs.down(self.current_position, button)
            self._held_owner = asyncio.current_task()
            self._set(S.IDLE)

    async def release(self, *, button="left"):
        async with self._operation(held=True) as generation:
            self._check(generation)
            self._set(S.PRESSING)
            await self.inputs.up(self.current_position, button)
            await self._cleanup()
            self._set(S.IDLE)

    async def cancel(self):
        self._invalidate(False)
        async with self._lock:
            await self._cleanup()

    async def reset_input_state(self):
        async with self._lock:
            await self._cleanup()

    async def synchronize(self, position=None):
        async with self._lock:
            if self.state == S.MANUAL_REQUIRED:
                raise CursorError("Exit manual mode after security revalidation first")
            await self._synchronize(position)

    async def _synchronize(self, position):
        if not self.enabled:
            raise CursorError("Cursor is disabled")
        if self._closed or self.page.is_closed():
            raise CursorError("Cannot synchronize a closed page")
        if position is None:
            self.current_position, self.position_known = None, False
            raise CursorError("Explicit viewport position required; browser cannot report physical pointer position")
        self.config.authorize(self.page.url)
        if await self._security_blocked():
            self._invalidate(True)
            await self._cleanup()
            self.current_position, self.position_known = None, False
            raise CursorError("Security hold prevents synchronization")
        if bound(position, await self._viewport()) != position:
            raise CursorError("Invalid synchronization position")
        await self._cleanup()
        self.generation_id += 1  # Recovery invalidates every previously queued action.
        self._geometry_snapshot = None
        self.position_known = False
        self._last_input_position = position
        await asyncio.wait_for(self.backend.move(position, self.input_state), self.config.actionability_timeout_ms/1000)
        self.current_position, self.position_known = position, True
        if getattr(self.page, "_autoapply_cursor", None) is self:
            self.page._autoapply_cursor_initialized = True
        self._set(S.IDLE)
        self._event("synchronized", position=vars(position))

    async def enter_manual_mode(self):
        self._invalidate(True)
        async with self._lock:
            try:
                await self._cleanup()
            finally:
                self.current_position, self.position_known = None, False

    async def exit_manual_mode(self, position=None):
        async with self._lock:
            if self.state != S.MANUAL_REQUIRED:
                raise CursorError("Cursor is not in manual mode")
            if await self._security_blocked():
                raise CursorError("Security hold remains active")
            await self._synchronize(position)

    async def native_control(self, operation, reason):
        """Choose this path BEFORE any physical click; never a retry after a failed click."""
        async with self._operation() as generation:
            self._event("framework_fallback", reason=reason)
            self._check(generation)
            result = await operation()
            self._check(generation)
            return result
