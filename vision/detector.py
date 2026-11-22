"""Person-down detector: posture classification + timed state machine.

Pure module: no cv2, no IO. Feed it one primary person's keypoints per frame with a
source timestamp in milliseconds; it returns the current status and, once per down
episode, an Alert.

States
  UPRIGHT  -> PENDING   first DOWN frame
  PENDING  -> ALERTED   DOWN held continuously for CONFIRM_DOWN_SECONDS -> emits Alert
  PENDING  -> UPRIGHT   any non-DOWN frame, UNKNOWN frame, or frame gap
  ALERTED  -> UPRIGHT   clearly upright continuously for REARM_UPRIGHT_SECONDS (rearm)

UNKNOWN frames (and gaps longer than MAX_FRAME_GAP_SECONDS) break the pending down
timer and the pending rearm timer, but never clear ALERTED.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

import config
from pose_math import Torso, torso_from_keypoints

Point = tuple[float, float]


class Posture(str, Enum):
    DOWN = "down"
    UPRIGHT = "upright"  # clearly upright: counts toward rearm
    OTHER = "other"  # visible, not down, not clearly upright (bend, half sit-up)
    UNKNOWN = "unknown"  # no person or weak torso keypoints


class State(str, Enum):
    UPRIGHT = "UPRIGHT"
    PENDING = "PENDING"
    ALERTED = "ALERTED"


@dataclass(frozen=True)
class DetectorConfig:
    keypoint_min_conf: float = config.KEYPOINT_MIN_CONF
    down_max_torso_deg: float = config.DOWN_MAX_TORSO_DEG
    upright_min_torso_deg: float = config.UPRIGHT_MIN_TORSO_DEG
    bbox_min_aspect: float = config.BBOX_MIN_ASPECT
    confirm_down_seconds: float = config.CONFIRM_DOWN_SECONDS
    rearm_upright_seconds: float = config.REARM_UPRIGHT_SECONDS
    max_frame_gap_seconds: float = config.MAX_FRAME_GAP_SECONDS


@dataclass(frozen=True)
class PostureReading:
    posture: Posture
    torso: Torso | None = None
    in_floor: bool | None = None  # both torso midpoints inside the floor polygon
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
    bbox_aspect: float | None = None  # width / height


@dataclass(frozen=True)
class Alert:
    episode_id: str
    signal: str
    detected_at_ms: float  # source timestamp of the confirming frame
    down_started_ms: float  # source timestamp of the first DOWN frame of this episode


@dataclass(frozen=True)
class Status:
    ts_ms: float
    state: State
    reading: PostureReading
    down_seconds: float  # progress toward confirmation (0 unless PENDING/ALERTED)
    rearm_seconds: float  # progress toward rearm (0 unless ALERTED)
    alert: Alert | None = None  # set only on the frame that confirms an episode
    episode_id: str | None = None  # current latched episode, if any


# ---------------------------------------------------------------- geometry


def point_in_polygon(pt: Point, polygon: Sequence[Point]) -> bool:
    """Ray casting. Points exactly on an edge may land either side; fine for this use."""
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def keypoint_bbox(xy, conf, min_conf: float):
    pts = [(float(p[0]), float(p[1])) for p, c in zip(xy, conf) if c >= min_conf]
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def classify_posture(
    xy,
    conf,
    floor_polygon: Sequence[Point] | None,
    cfg: DetectorConfig = DetectorConfig(),
) -> PostureReading:
    """Classify one person's keypoints. floor_polygon=None means the whole frame is floor."""
    if xy is None or conf is None:
        return PostureReading(Posture.UNKNOWN)
    torso = torso_from_keypoints(xy, conf, cfg.keypoint_min_conf)
    if torso is None:
        return PostureReading(Posture.UNKNOWN)

    bbox = keypoint_bbox(xy, conf, cfg.keypoint_min_conf)
    aspect = None
    if bbox is not None:
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        aspect = w / h if h > 0 else float("inf")

    if floor_polygon is None:
        in_floor = True
    else:
        in_floor = point_in_polygon(torso.shoulder_mid, floor_polygon) and point_in_polygon(
            torso.hip_mid, floor_polygon
        )

    is_down = (
        torso.angle_deg <= cfg.down_max_torso_deg
        and in_floor
        and aspect is not None
        and aspect > cfg.bbox_min_aspect
    )
    if is_down:
        posture = Posture.DOWN
    elif torso.angle_deg >= cfg.upright_min_torso_deg:
        posture = Posture.UPRIGHT
    else:
        posture = Posture.OTHER
    return PostureReading(posture, torso, in_floor, bbox, aspect)


# ---------------------------------------------------------------- state machine


def _default_episode_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class PersonDownDetector:
    floor_polygon: Sequence[Point] | None = None
    cfg: DetectorConfig = field(default_factory=DetectorConfig)
    new_episode_id: Callable[[], str] = _default_episode_id
    signal: str = "possible_person_down"

    state: State = State.UPRIGHT
    down_since_ms: float | None = None
    upright_since_ms: float | None = None
    episode_id: str | None = None
    last_ts_ms: float | None = None

    def reset(self) -> None:
        """Clear pending and latched state (e.g. switching to the fallback clip)."""
        self.state = State.UPRIGHT
        self.down_since_ms = None
        self.upright_since_ms = None
        self.episode_id = None
        self.last_ts_ms = None

    def update(self, ts_ms: float, xy=None, conf=None) -> Status:
        reading = classify_posture(xy, conf, self.floor_polygon, self.cfg)
        return self.update_reading(ts_ms, reading)

    def update_reading(self, ts_ms: float, reading: PostureReading) -> Status:
        gap = (
            self.last_ts_ms is not None
            and (ts_ms - self.last_ts_ms) / 1000.0 > self.cfg.max_frame_gap_seconds
        )
        self.last_ts_ms = ts_ms
        posture = reading.posture
        alert = None

        if self.state is State.ALERTED:
            # Latched. Only sustained clear upright rearms; nothing else clears it.
            if posture is Posture.UPRIGHT and not gap:
                if self.upright_since_ms is None:
                    self.upright_since_ms = ts_ms
            elif posture is Posture.UPRIGHT:  # upright after a gap: restart the timer here
                self.upright_since_ms = ts_ms
            else:
                self.upright_since_ms = None
            if (
                self.upright_since_ms is not None
                and (ts_ms - self.upright_since_ms) / 1000.0 >= self.cfg.rearm_upright_seconds
            ):
                self.state = State.UPRIGHT
                self.upright_since_ms = None
                self.down_since_ms = None
                self.episode_id = None
        else:
            if posture is Posture.DOWN:
                if self.state is State.UPRIGHT or gap:
                    self.state = State.PENDING
                    self.down_since_ms = ts_ms
                if (ts_ms - self.down_since_ms) / 1000.0 >= self.cfg.confirm_down_seconds:
                    self.state = State.ALERTED
                    self.episode_id = self.new_episode_id()
                    self.upright_since_ms = None
                    alert = Alert(self.episode_id, self.signal, ts_ms, self.down_since_ms)
            else:
                # Not down, UNKNOWN, or anything else breaks the pending timer.
                self.state = State.UPRIGHT
                self.down_since_ms = None

        down_s = 0.0
        if self.state in (State.PENDING, State.ALERTED) and self.down_since_ms is not None:
            down_s = (ts_ms - self.down_since_ms) / 1000.0
        rearm_s = 0.0
        if self.state is State.ALERTED and self.upright_since_ms is not None:
            rearm_s = (ts_ms - self.upright_since_ms) / 1000.0
        return Status(ts_ms, self.state, reading, down_s, rearm_s, alert, self.episode_id)
