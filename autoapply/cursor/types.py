"""Cursor coordinates are always main-frame viewport CSS pixels."""
from dataclasses import dataclass, field
from enum import StrEnum
import math
from urllib.parse import urlsplit


class CursorError(RuntimeError):
    pass


class CursorCancelledError(CursorError):
    pass


class TargetUnavailableError(CursorError):
    pass


class TargetUnstableError(CursorError):
    pass


class CursorState(StrEnum):
    IDLE = "IDLE"
    RESOLVING = "RESOLVING"
    ACTIONABILITY_CHECK = "ACTIONABILITY_CHECK"
    SCROLLING = "SCROLLING"
    STABILIZING = "STABILIZING"
    MOVING = "MOVING"
    REVALIDATING = "REVALIDATING"
    PRESSING = "PRESSING"
    DRAGGING = "DRAGGING"
    WAITING_FOR_RESULT = "WAITING_FOR_RESULT"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class TimedPoint(Point):
    time_ms: float


@dataclass
class MouseInputState:
    buttons: set[str] = field(default_factory=set)
    modifiers: set[str] = field(default_factory=set)
    click_count: int = 1
    pointer_type: str = "mouse"


@dataclass(frozen=True)
class CursorConfig:
    enabled: bool = True
    perform_physical_clicks: bool = True
    backend: str = "playwright"
    mode: str = "restricted"
    owned_origins: tuple[str, ...] = ()
    target_strategy: str = "SAFE_CENTER"
    target_margin_ratio: float = .20
    max_point_attempts: int = 10
    actionability_timeout_ms: int = 3000
    geometry_sample_interval_ms: int = 40
    geometry_stable_samples: int = 3
    geometry_tolerance_px: float = 1.5
    geometry_timeout_ms: int = 1500
    target_move_tolerance_px: float = 10
    max_replans: int = 3
    min_steps: int = 12
    max_steps: int = 60
    pixels_per_step: float = 12
    base_duration_ms: float = 120
    duration_per_pixel_ms: float = .75
    min_duration_ms: float = 250
    max_duration_ms: float = 1000
    min_frame_delay_ms: float = 4
    max_frame_delay_ms: float = 50
    max_curve_offset_px: float = 120
    easing: str = "easeInOutCubic"
    noise_enabled: bool = False
    noise_px: float = 1.5
    overshoot_enabled: bool = False
    overshoot_probability: float = .15
    max_overshoot_px: float = 12
    timing_variation: bool = False
    curve_variation: bool = False
    press_duration_ms: float = 50
    midflight_threshold_ms: float = 500
    revalidation_interval_ms: float = 200

    def __post_init__(self):
        if self.backend not in {"playwright", "cdp"} or self.mode not in {"restricted", "owned_test"}:
            raise ValueError("Invalid cursor backend/mode")
        if self.target_strategy not in {"SAFE_CENTER", "UNIFORM_INTERIOR", "GAUSSIAN_INTERIOR"}:
            raise ValueError("Invalid cursor target strategy")
        if self.easing not in {"easeInOutCubic", "easeOutCubic", "smoothstep"}:
            raise ValueError("Invalid cursor easing")
        for name, value in vars(self).items():
            if isinstance(value, (float, int)) and not isinstance(value, bool) and (not math.isfinite(value) or value < 0):
                raise ValueError("Invalid cursor numeric setting: " + name)
        for name in ("min_steps", "max_steps", "max_replans", "max_point_attempts", "geometry_stable_samples"):
            if type(getattr(self, name)) is not int:
                raise ValueError("Cursor count must be an integer: " + name)
        for name in ("enabled", "perform_physical_clicks", "noise_enabled", "overshoot_enabled", "timing_variation", "curve_variation"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Cursor switch must be a boolean: " + name)
        if not 0 <= self.target_margin_ratio < .5 or not 0 <= self.overshoot_probability <= 1:
            raise ValueError("Invalid cursor ratio")
        if not 2 <= self.min_steps <= self.max_steps <= 500 or self.pixels_per_step <= 0:
            raise ValueError("Invalid cursor sampling bounds")
        if not 0 < self.min_duration_ms <= self.max_duration_ms or not 4 <= self.min_frame_delay_ms <= self.max_frame_delay_ms <= 50:
            raise ValueError("Invalid cursor timing bounds")
        if not 0 <= self.press_duration_ms <= 1000 or self.max_replans > 10:
            raise ValueError("Invalid cursor press/replan bounds")
        if min(self.geometry_stable_samples, self.geometry_sample_interval_ms, self.geometry_timeout_ms,
               self.actionability_timeout_ms, self.max_point_attempts, self.revalidation_interval_ms) <= 0:
            raise ValueError("Cursor validation bounds must be positive")
        if self.randomized and self.mode != "owned_test":
            raise ValueError("Presentation randomization requires owned_test mode")
        if self.mode == "owned_test" and not self.owned_origins:
            raise ValueError("owned_test mode requires an explicit origin allowlist")
        for origin in self.owned_origins:
            if not normalized_origin(origin) or normalized_origin(origin) != origin:
                raise ValueError("Allowlist must contain exact HTTP(S) origins")

    @property
    def randomized(self):
        return (self.target_strategy != "SAFE_CENTER" or self.noise_enabled or self.overshoot_enabled
                or self.timing_variation or self.curve_variation)

    def authorize(self, url):
        if self.mode == "owned_test" and normalized_origin(url) not in self.owned_origins:
            raise CursorError("Cursor owned/test mode denied on this origin")


def normalized_origin(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    port = parts.port
    host = parts.hostname
    if ":" in host:
        host = "[" + host + "]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else "")
