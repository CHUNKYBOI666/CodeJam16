"""Person-down detector: posture classification + timed state machine.

Pure module: no cv2, no IO. Feed it the people seen in each frame (or one person's
keypoints) with a source timestamp in milliseconds; it returns the current status and,
once per down episode, an Alert.

States
  UPRIGHT  -> PENDING   first DOWN frame
  PENDING  -> ALERTED   DOWN held continuously for CONFIRM_DOWN_SECONDS -> emits Alert
  PENDING  -> UPRIGHT   any non-DOWN frame, UNKNOWN frame, or frame gap
  ALERTED  -> UPRIGHT   clearly upright continuously for REARM_UPRIGHT_SECONDS (rearm)

UNKNOWN frames (and gaps longer than MAX_FRAME_GAP_SECONDS) break the pending down
timer and the pending rearm timer, but never clear ALERTED. Every UNKNOWN reading
carries a reason (see UnknownReason).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Sequence

import config
from pose_math import Torso, torso_from_keypoints

Point = tuple[float, float]

L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 13, 14, 15, 16


class Posture(str, Enum):
    DOWN = "down"
    UPRIGHT = "upright"  # clearly upright: counts toward rearm
    OTHER = "other"  # visible, not down, not clearly upright (bend, sitting on the floor)
    UNKNOWN = "unknown"  # see UnknownReason


class UnknownReason(str, Enum):
    NO_PERSON = "no_person"
    MULTIPLE_PEOPLE = "multiple_people"
    WEAK_TORSO = "weak_torso"  # a shoulder or hip keypoint below KEYPOINT_MIN_CONF
    TORSO_OFF_FRAME = "torso_off_frame"
    DEGENERATE_TORSO = "degenerate_torso"  # shoulder-mid == hip-mid


class State(str, Enum):
    UPRIGHT = "UPRIGHT"
    PENDING = "PENDING"
    ALERTED = "ALERTED"


@dataclass(frozen=True)
class DetectorConfig:
    keypoint_min_conf: float = config.KEYPOINT_MIN_CONF
    down_max_torso_deg: float = config.DOWN_MAX_TORSO_DEG
    upright_min_torso_deg: float = config.UPRIGHT_MIN_TORSO_DEG
    upright_min_hip_ankle_ratio: float = config.UPRIGHT_MIN_HIP_ANKLE_RATIO
    upright_min_hip_knee_ratio: float = config.UPRIGHT_MIN_HIP_KNEE_RATIO
    bbox_min_aspect: float = config.BBOX_MIN_ASPECT
    other_person_min_conf: float = config.OTHER_PERSON_MIN_CONF
    confirm_down_seconds: float = config.CONFIRM_DOWN_SECONDS
    rearm_upright_seconds: float = config.REARM_UPRIGHT_SECONDS
    max_frame_gap_seconds: float = config.MAX_FRAME_GAP_SECONDS


@dataclass(frozen=True)
class Person:
    """One detected person: 17 COCO keypoints (xy, conf) and the person-box score."""

    xy: Sequence[Point]
    conf: Sequence[float]
    score: float = 1.0


@dataclass(frozen=True)
class PostureReading:
    posture: Posture
    torso: Torso | None = None  # kept for display even when posture is UNKNOWN(multiple_people)
    in_floor: bool | None = None  # both torso midpoints inside the floor polygon
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
    bbox_aspect: float | None = None  # width / height
    reason: UnknownReason | None = None  # set iff posture is UNKNOWN
    people: int = 0  # people detected in the frame


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


def alert_event(
    alert: Alert,
    *,
    camera_id: str,
    zone_id: str,
    source: str,
    detected_at: datetime,
) -> dict:
    """The staff-app event for an alert, with the fields of fixtures/person-down.json."""
    if detected_at.tzinfo is None:
        raise ValueError("detected_at must be timezone-aware")
    ts = detected_at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "episode_id": alert.episode_id,
        "signal": alert.signal,
        "camera_id": camera_id,
        "zone_id": zone_id,
        "detected_at": ts,
        "source": source,
    }


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


def _mid_y(xy, conf, a: int, b: int, min_conf: float) -> float | None:
    """Mean y of the confident points among a, b (either side is enough)."""
    ys = [float(xy[i][1]) for i in (a, b) if conf[i] >= min_conf]
    return sum(ys) / len(ys) if ys else None


def _is_upright(torso: Torso, xy, conf, cfg: DetectorConfig) -> bool:
    if torso.angle_deg < cfg.upright_min_torso_deg:
        return False
    if torso.shoulder_mid[1] >= torso.hip_mid[1]:  # image y grows downward: head-down
        return False
    torso_len = math.dist(torso.shoulder_mid, torso.hip_mid)
    hip_y = torso.hip_mid[1]
    ankle_y = _mid_y(xy, conf, L_ANKLE, R_ANKLE, cfg.keypoint_min_conf)
    if ankle_y is not None:
        return ankle_y - hip_y >= cfg.upright_min_hip_ankle_ratio * torso_len
    knee_y = _mid_y(xy, conf, L_KNEE, R_KNEE, cfg.keypoint_min_conf)
    if knee_y is not None:
        return knee_y - hip_y >= cfg.upright_min_hip_knee_ratio * torso_len
    return True  # legs not visible (e.g. behind furniture): fall back to the torso checks


def classify_posture(
    xy,
    conf,
    floor_polygon: Sequence[Point] | None,
    cfg: DetectorConfig = DetectorConfig(),
    frame_size: tuple[int, int] | None = None,
) -> PostureReading:
    """Classify one person's keypoints. floor_polygon=None means the whole frame is floor.

    frame_size=(width, height) enables the torso-inside-frame check.
    """
    if xy is None or conf is None:
        return PostureReading(Posture.UNKNOWN, reason=UnknownReason.NO_PERSON)
    if any(conf[i] < cfg.keypoint_min_conf for i in config.TORSO_KEYPOINTS):
        return PostureReading(Posture.UNKNOWN, reason=UnknownReason.WEAK_TORSO, people=1)
    if frame_size is not None:
        w, h = frame_size
        for i in config.TORSO_KEYPOINTS:
            x, y = float(xy[i][0]), float(xy[i][1])
            if not (0 <= x < w and 0 <= y < h):
                return PostureReading(Posture.UNKNOWN, reason=UnknownReason.TORSO_OFF_FRAME, people=1)
    torso = torso_from_keypoints(xy, conf, cfg.keypoint_min_conf)
    if torso is None:
        return PostureReading(Posture.UNKNOWN, reason=UnknownReason.DEGENERATE_TORSO, people=1)

    bbox = keypoint_bbox(xy, conf, cfg.keypoint_min_conf)
    aspect = None
    if bbox is not None:
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        aspect = bw / bh if bh > 0 else float("inf")

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
    elif _is_upright(torso, xy, conf, cfg):
        posture = Posture.UPRIGHT
    else:
        posture = Posture.OTHER
    return PostureReading(posture, torso, in_floor, bbox, aspect, people=1)


def classify_people(
    people: Sequence[Person] | None,
    floor_polygon: Sequence[Point] | None,
    cfg: DetectorConfig = DetectorConfig(),
    frame_size: tuple[int, int] | None = None,
) -> PostureReading:
    """One reading for the whole frame.

    - nobody: UNKNOWN(no_person)
    - anyone DOWN: DOWN, using the most confident down person
    - two or more people scoring >= other_person_min_conf: UNKNOWN(multiple_people)
    - otherwise: the most confident person's reading
    """
    if not people:
        return PostureReading(Posture.UNKNOWN, reason=UnknownReason.NO_PERSON)
    n = len(people)
    readings = [(p, classify_posture(p.xy, p.conf, floor_polygon, cfg, frame_size)) for p in people]
    downs = [(p, r) for p, r in readings if r.posture is Posture.DOWN]
    if downs:
        _, r = max(downs, key=lambda pr: pr[0].score)
        return replace(r, people=n)
    best_p, best_r = max(readings, key=lambda pr: pr[0].score)
    if sum(p.score >= cfg.other_person_min_conf for p in people) > 1:
        return replace(best_r, posture=Posture.UNKNOWN, reason=UnknownReason.MULTIPLE_PEOPLE, people=n)
    return replace(best_r, people=n)


# ---------------------------------------------------------------- state machine


def _default_episode_id() -> str:
    return str(uuid.uuid4())


@dataclass
class PersonDownDetector:
    floor_polygon: Sequence[Point] | None = None
    cfg: DetectorConfig = field(default_factory=DetectorConfig)
    new_episode_id: Callable[[], str] = _default_episode_id
    signal: str = config.ALERT_SIGNAL
    frame_size: tuple[int, int] | None = None  # (width, height) for the torso-in-frame check

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
        """Single-person convenience: one person's keypoints, or None for nobody."""
        people = None if xy is None or conf is None else [Person(xy, conf)]
        return self.update_people(ts_ms, people)

    def update_people(self, ts_ms: float, people: Sequence[Person] | None) -> Status:
        reading = classify_people(people, self.floor_polygon, self.cfg, self.frame_size)
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
