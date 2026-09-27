"""Fresh locator resolution and hit testing, including ancestor-frame occlusion."""
import asyncio
import time
from ..scrolling import ScrollController
from dataclasses import dataclass
from .planning import clamp
from .types import Point, TargetUnavailableError, TargetUnstableError


def box_difference(a, b):
    return max(abs(a[k]-b[k]) for k in ("x", "y", "width", "height"))


class ActionabilityValidator:
    async def check(self, locator, timeout):
        if await locator.count() != 1:
            raise TargetUnavailableError("Cursor target must be singular")
        if not await locator.is_visible() or not await locator.is_enabled():
            raise TargetUnavailableError("Cursor target is hidden or disabled")
        if not await locator.evaluate("e => e.isConnected", timeout=timeout):
            raise TargetUnavailableError("Cursor target detached")
        # Playwright's trial click still moves the mouse. Use explicit visibility,
        # enabled, connected, stability and hit-test checks without that side effect.


class GeometryValidator:
    def __init__(self, config):
        self.config = config

    async def stable(self, locator, check):
        c = self.config
        deadline = time.monotonic()+c.geometry_timeout_ms/1000
        previous, samples = None, 0
        while time.monotonic() < deadline:
            check()
            current = await asyncio.wait_for(
                locator.bounding_box(timeout=min(c.actionability_timeout_ms, max(1,(deadline-time.monotonic())*1000))),
                timeout=max(.001,deadline-time.monotonic()))
            check()
            if not current or min(current["width"], current["height"]) <= 0:
                raise TargetUnavailableError("Cursor target has no geometry")
            samples = samples+1 if previous and box_difference(previous, current) <= c.geometry_tolerance_px else 0
            if samples >= c.geometry_stable_samples:
                return current
            previous = current
            await asyncio.sleep(c.geometry_sample_interval_ms/1000)
        raise TargetUnstableError("Cursor target never stabilized")


# Handle is transient: identity retained by the controller is always a Locator.
# Bounding boxes are main-frame CSS px; local rects are used ONLY for hit tests.
HIT_TEST = """(e, p) => {
  if (!e.isConnected) return false;
  let hit = e.ownerDocument.elementFromPoint(p.x, p.y);
  while (hit?.shadowRoot) {
    const inner = hit.shadowRoot.elementFromPoint(p.x,p.y);
    if (!inner || inner === hit) break;
    hit = inner;
  }
  for (let node=hit; node; node=node.parentNode || node.host) {
    if (node === e) return true;
  }
  return false;
}"""


class HitTestValidator:
    async def valid(self, locator, point):
        handle = await locator.element_handle()
        if handle is None:
            return False
        ancestors = []
        try:
            frame = await handle.owner_frame()
            while frame and frame.parent_frame:
                element = await frame.frame_element()
                ancestors.append(element)
                frame = frame.parent_frame
            local = point
            # Descend outermost first; no offsets are added to physical mouse coordinates.
            for element in reversed(ancestors):
                if not await element.evaluate(HIT_TEST, vars(local)):
                    return False
                geometry = await element.evaluate("""e => {
                  // Reject non-axis-aligned transforms on frame or ancestors.
                  for (let n=e; n instanceof Element; n=n.parentElement) {
                    const s=getComputedStyle(n), m=new DOMMatrix(s.transform);
                    if (!m.is2D || m.b || m.c || m.a <= 0 || m.d <= 0 ||
                        (s.rotate && s.rotate !== 'none' && s.rotate !== '0deg') ||
                        (s.perspective && s.perspective !== 'none')) return null;
                  }
                  const r=e.getBoundingClientRect();
                  return {x:r.x,y:r.y,sx:r.width/e.offsetWidth,sy:r.height/e.offsetHeight,
                          bx:e.clientLeft,by:e.clientTop};
                }""")
                if not geometry or not geometry["sx"] or not geometry["sy"]:
                    return False
                local = Point((local.x-geometry["x"])/geometry["sx"]-geometry["bx"],
                              (local.y-geometry["y"])/geometry["sy"]-geometry["by"])
            return await handle.evaluate(HIT_TEST, vars(local))
        finally:
            for ancestor in ancestors:
                await ancestor.dispose()
            await handle.dispose()


@dataclass
class ResolvedTarget:
    locator: object
    point: Point
    box: dict
    geometry_generation: int


class TargetResolver:
    def __init__(self, config, rng):
        self.config, self.rng = config, rng
        self.actionability = ActionabilityValidator()
        self.geometry = GeometryValidator(config)
        self.hit_test = HitTestValidator()

    async def scroll(self, locator, check=lambda: None, guard=None):
        handle = await locator.element_handle(timeout=self.config.actionability_timeout_ms)
        if handle is None:
            raise TargetUnavailableError("Cursor target detached")
        ancestors = []
        try:
            frame = await handle.owner_frame()
            while frame:
                check()
                self.config.authorize(frame.url)
                if frame.parent_frame:
                    ancestors.append(await frame.frame_element())
                frame = frame.parent_frame
            # Offscreen child frames may have throttled animation frames, preventing
            # Playwright's inner stability check. Expose outer frames first.
            for element in reversed(ancestors):
                check()
                await ScrollController((await element.owner_frame()).page, guard or check).ensure_visible(element)
            check()
            await ScrollController((await handle.owner_frame()).page, guard or check).ensure_visible(locator)
            check()
        finally:
            for element in ancestors:
                await element.dispose()
            await handle.dispose()

    def candidate(self, box, attempt):
        c = self.config
        left, top = box["x"]+box["width"]*c.target_margin_ratio, box["y"]+box["height"]*c.target_margin_ratio
        width, height = box["width"]*(1-2*c.target_margin_ratio), box["height"]*(1-2*c.target_margin_ratio)
        if c.target_strategy == "UNIFORM_INTERIOR":
            return Point(left+self.rng.random()*width, top+self.rng.random()*height)
        if c.target_strategy == "GAUSSIAN_INTERIOR":
            return Point(clamp(self.rng.gauss(left+width/2, width/6), left, left+width),
                         clamp(self.rng.gauss(top+height/2, height/6), top, top+height))
        offsets = [(0,0), (-.3,0), (.3,0), (0,-.3), (0,.3), (-.3,-.3), (.3,-.3), (-.3,.3), (.3,.3), (.15,.15)]
        x, y = offsets[attempt % len(offsets)]
        return Point(left+width*(.5+x), top+height*(.5+y))

    async def select(self, locator, box, generation, viewport):
        for attempt in range(self.config.max_point_attempts):
            point = self.candidate(box, attempt)
            if 0 <= point.x < viewport[0] and 0 <= point.y < viewport[1] and await self.hit_test.valid(locator, point):
                return ResolvedTarget(locator, point, box, generation)
        raise TargetUnavailableError("No safe clickable point")
