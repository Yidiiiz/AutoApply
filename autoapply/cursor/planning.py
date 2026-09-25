"""Pure, seedable presentation planning; no security-dependent adaptation."""
import math
import random
from .types import Point, TimedPoint, CursorError


def clamp(value, low, high):
    return min(high, max(low, value))


def bound(point, viewport):
    return Point(clamp(point.x, 0, viewport[0] - .01), clamp(point.y, 0, viewport[1] - .01))


def easing(t, name):
    if name == "easeOutCubic":
        return 1 - (1 - t) ** 3
    if name == "smoothstep":
        return t * t * (3 - 2 * t)
    return 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def cubic_bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return Point(*(u**3 * a + 3*u*u*t*b + 3*u*t*t*c + t**3*d
                   for a, b, c, d in zip((p0.x, p0.y), (p1.x, p1.y), (p2.x, p2.y), (p3.x, p3.y))))


class TimingProfile:
    def __init__(self, config, rng):
        self.config, self.rng = config, rng

    def duration(self, distance):
        c = self.config
        value = c.base_duration_ms + distance * c.duration_per_pixel_ms
        if c.timing_variation:
            value *= self.rng.uniform(.9, 1.1)
        return clamp(value, c.min_duration_ms, c.max_duration_ms)

    def press_duration(self):
        return self.rng.uniform(30, 90) if self.config.timing_variation else self.config.press_duration_ms


class NoiseProfile:
    def __init__(self, rng, amplitude):
        self.x = [rng.uniform(-1, 1) for _ in range(5)]
        self.y = [rng.uniform(-1, 1) for _ in range(5)]
        self.amplitude = amplitude

    def offset(self, t):
        def smooth(anchors):
            scaled = t * (len(anchors) - 1)
            i = min(int(scaled), len(anchors) - 2)
            f = easing(scaled - i, "smoothstep")
            return (anchors[i] * (1-f) + anchors[i+1] * f) * self.amplitude * math.sin(math.pi*t)
        return Point(smooth(self.x), smooth(self.y))


class OvershootProfile:
    def destination(self, start, end, viewport, config, rng):
        distance = start.distance(end)
        if not config.overshoot_enabled or distance < 1 or rng.random() >= config.overshoot_probability:
            return end
        amount = rng.uniform(0, min(config.max_overshoot_px, distance * .1))
        return bound(Point(end.x + (end.x-start.x)/distance*amount,
                           end.y + (end.y-start.y)/distance*amount), viewport)


class BezierPlanner:
    def __init__(self, config, rng=None):
        self.config, self.rng = config, rng if rng is not None else random.Random()
        self.timing = TimingProfile(config, self.rng)

    def plan(self, start, end, viewport):
        c = self.config
        if bound(start, viewport) != start or bound(end, viewport) != end:
            raise CursorError("Cursor endpoints outside viewport; synchronize explicitly")
        distance = start.distance(end)
        duration = self.timing.duration(distance)
        low = max(c.min_steps, math.ceil(duration/c.max_frame_delay_ms)+1)
        high = min(c.max_steps, math.floor(duration/c.min_frame_delay_ms)+1)
        if low > high:
            raise CursorError("Incompatible cursor sample/timing bounds")
        count = int(clamp(round(distance/c.pixels_per_step), low, high))
        destination = OvershootProfile().destination(start, end, viewport, c, self.rng)
        dx, dy = destination.x-start.x, destination.y-start.y
        length = max(1, math.hypot(dx, dy))
        offset = min(c.max_curve_offset_px, distance*.15)
        if c.curve_variation:
            offset *= self.rng.uniform(-1, 1)
        p1 = bound(Point(start.x+dx*.33-dy/length*offset, start.y+dy*.33+dx/length*offset), viewport)
        p2 = bound(Point(start.x+dx*.66-dy/length*offset*.5, start.y+dy*.66+dx/length*offset*.5), viewport)
        noise = NoiseProfile(self.rng, c.noise_px) if c.noise_enabled else None
        points = []
        for i in range(count):
            t = i/(count-1)
            if destination != end and t > .8:
                f = easing((t-.8)/.2, "smoothstep")
                point = Point(destination.x+(end.x-destination.x)*f, destination.y+(end.y-destination.y)*f)
            else:
                point = cubic_bezier(start, p1, p2, destination, easing(t/(.8 if destination != end else 1), c.easing))
            if noise:
                n = noise.offset(t)
                point = bound(Point(point.x+n.x, point.y+n.y), viewport)
            if i == 0:
                point = start
            elif i == count-1:
                point = end
            points.append(TimedPoint(point.x, point.y, duration*t))
        self.validate(points, start, end, viewport)
        return points

    def validate(self, points, start, end, viewport):
        c = self.config
        if not c.min_steps <= len(points) <= c.max_steps:
            raise CursorError("Invalid path sample count")
        if Point(points[0].x, points[0].y) != start or Point(points[-1].x, points[-1].y) != end:
            raise CursorError("Invalid path endpoints")
        if points[0].time_ms != 0 or not c.min_duration_ms <= points[-1].time_ms <= c.max_duration_ms:
            raise CursorError("Invalid path duration")
        for i, p in enumerate(points):
            if not all(math.isfinite(v) for v in (p.x, p.y, p.time_ms)) or bound(p, viewport) != Point(p.x, p.y):
                raise CursorError("Invalid path coordinate")
            if i:
                delta = p.time_ms-points[i-1].time_ms
                if not c.min_frame_delay_ms-.001 <= delta <= c.max_frame_delay_ms+.001:
                    raise CursorError("Invalid path timing")
                # Cubic easing derivative <=3; allow bounded curves/settling and noise.
                limit = 8*(start.distance(end)+2*c.max_curve_offset_px+c.max_overshoot_px)/(len(points)-1)+4*c.noise_px
                if p.distance(points[i-1]) > limit:
                    raise CursorError("Path segment exceeds movement bound")


PathPlanner = BezierPlanner
