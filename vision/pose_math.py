"""Pure torso geometry (no cv2 / IO) so the detector can be unit tested on fake keypoints."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import (
    KEYPOINT_MIN_CONF,
    L_HIP,
    L_SHOULDER,
    R_HIP,
    R_SHOULDER,
    TORSO_KEYPOINTS,
)


@dataclass(frozen=True)
class Torso:
    shoulder_mid: tuple[float, float]
    hip_mid: tuple[float, float]
    angle_deg: float  # 0 = horizontal (lying), 90 = vertical (upright)


def _mid(a, b) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def torso_from_keypoints(xy, conf, min_conf: float = KEYPOINT_MIN_CONF) -> Torso | None:
    """Return the torso line for one person, or None (UNKNOWN) if any torso point is weak.

    xy: sequence of 17 (x, y) pixel coords; conf: sequence of 17 confidences.
    """
    if conf is None or any(conf[i] < min_conf for i in TORSO_KEYPOINTS):
        return None
    shoulder_mid = _mid(xy[L_SHOULDER], xy[R_SHOULDER])
    hip_mid = _mid(xy[L_HIP], xy[R_HIP])
    dx = hip_mid[0] - shoulder_mid[0]
    dy = hip_mid[1] - shoulder_mid[1]
    if dx == 0 and dy == 0:
        return None
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    return Torso(shoulder_mid, hip_mid, angle)
