"""Convert local pose observations into one event per sustained down episode."""

from datetime import datetime, timezone
from math import atan2, degrees, isfinite
from numbers import Real
from uuid import uuid4


# Indices in the COCO pose model's 17-keypoint layout.
_LEFT_SHOULDER, _RIGHT_SHOULDER, _LEFT_HIP, _RIGHT_HIP = 5, 6, 11, 12


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _torso_midpoints(results, frame_bgr, minimum_confidence):
    """Extract shoulder and hip midpoints or return a reason for unknown posture."""
    if frame_bgr is None:
        return None, "missing_frame"

    people = []
    for result in results:
        keypoints = result.keypoints
        if keypoints is None:
            continue
        people.extend((keypoints, index) for index in range(len(keypoints.xy)))
        if len(people) > 1:
            return None, "multiple_people"
    if not people:
        return None, "no_person"

    keypoints, person_index = people[0]
    height, width = frame_bgr.shape[:2]
    torso_points = []
    try:
        for index in (_LEFT_SHOULDER, _RIGHT_SHOULDER, _LEFT_HIP, _RIGHT_HIP):
            confidence = float(keypoints.conf[person_index][index])
            x, y = map(float, keypoints.xy[person_index][index])
            if not (
                isfinite(confidence)
                and minimum_confidence <= confidence <= 1
                and 0 <= x < width
                and 0 <= y < height
            ):
                return None, "unreliable_torso"
            torso_points.append((x, y))
    except (IndexError, TypeError, ValueError):
        return None, "unreliable_torso"

    left_shoulder, right_shoulder, left_hip, right_hip = torso_points
    shoulder = tuple((left + right) / 2 for left, right in zip(left_shoulder, right_shoulder))
    hip = tuple((left + right) / 2 for left, right in zip(left_hip, right_hip))
    if shoulder == hip:
        return None, "degenerate_torso"
    return (shoulder, hip), None


class PersonDownDetector:
    """Process strictly ordered source frames from one fixed camera."""

    def __init__(
        self,
        predict_pose,
        *,
        floor_box,
        camera_id="demo-camera",
        zone_id="demo-area",
        source="replay",
        confirmation_seconds=2.0,
        rearm_seconds=2.0,
        maximum_observation_gap_ms=500,
        minimum_keypoint_confidence=0.4,
        down_angle_degrees=30,
        upright_angle_degrees=60,
        observe_pose=None,
    ):
        if not callable(predict_pose):
            raise ValueError("predict_pose must be callable")
        try:
            left, top, right, bottom = (
                _finite_number(value, "floor_box coordinate") for value in floor_box
            )
        except (TypeError, ValueError) as error:
            raise ValueError("floor_box must contain four finite normalized coordinates") from error
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("floor_box must have increasing x and y coordinates in [0, 1]")

        confirmation_ms = _finite_number(confirmation_seconds, "confirmation_seconds") * 1000
        rearm_ms = _finite_number(rearm_seconds, "rearm_seconds") * 1000
        max_gap_ms = _finite_number(maximum_observation_gap_ms, "maximum_observation_gap_ms")
        confidence = _finite_number(minimum_keypoint_confidence, "minimum_keypoint_confidence")
        down_angle = _finite_number(down_angle_degrees, "down_angle_degrees")
        upright_angle = _finite_number(upright_angle_degrees, "upright_angle_degrees")
        if not (0 < confirmation_ms < float("inf") and 0 < rearm_ms < float("inf")):
            raise ValueError(
                "confirmation_seconds and rearm_seconds must be positive usable durations"
            )
        if max_gap_ms <= 0:
            raise ValueError("maximum_observation_gap_ms must be positive")
        if not 0 < confidence <= 1:
            raise ValueError("minimum_keypoint_confidence must be in (0, 1]")
        if not 0 <= down_angle < upright_angle <= 90:
            raise ValueError(
                "angles must satisfy 0 <= down_angle_degrees < upright_angle_degrees <= 90"
            )
        for name, value in (("camera_id", camera_id), ("zone_id", zone_id), ("source", source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")

        self._predict_pose = predict_pose
        self._observe_pose = observe_pose
        self._floor_box = (left, top, right, bottom)
        self._camera_id = camera_id
        self._zone_id = zone_id
        self._source = source
        self._confirmation_ms = confirmation_ms
        self._rearm_ms = rearm_ms
        self._maximum_observation_gap_ms = max_gap_ms
        self._minimum_keypoint_confidence = confidence
        self._down_angle_degrees = down_angle
        self._upright_angle_degrees = upright_angle

        self._last_timestamp_ms = None
        self._evidence_started_ms = None
        self._latched = False
        self.last_unknown_reason = None

    def _classify(self, frame_bgr, timestamp_ms):
        results = tuple(self._predict_pose(frame_bgr)) if frame_bgr is not None else ()
        if self._observe_pose is not None and frame_bgr is not None:
            self._observe_pose(timestamp_ms, frame_bgr.shape[:2], results)
        midpoints, self.last_unknown_reason = _torso_midpoints(
            results, frame_bgr, self._minimum_keypoint_confidence
        )
        if midpoints is None:
            return "unknown"

        shoulder, hip = midpoints
        angle = degrees(atan2(abs(shoulder[1] - hip[1]), abs(shoulder[0] - hip[0])))
        height, width = frame_bgr.shape[:2]
        left, top, right, bottom = self._floor_box
        if angle <= self._down_angle_degrees and all(
            left <= x / width <= right and top <= y / height <= bottom for x, y in midpoints
        ):
            return "down"
        if angle >= self._upright_angle_degrees and shoulder[1] < hip[1] < top * height:
            return "upright"
        return "ordinary"

    def update(self, frame_bgr, source_timestamp_ms):
        """Return a newly confirmed event, or None for any other observation."""
        timestamp_ms = _finite_number(source_timestamp_ms, "source_timestamp_ms")
        previous_timestamp_ms = self._last_timestamp_ms
        if timestamp_ms < 0 or (
            previous_timestamp_ms is not None and timestamp_ms <= previous_timestamp_ms
        ):
            raise ValueError("source_timestamp_ms must be nonnegative and strictly increasing")

        posture = self._classify(frame_bgr, timestamp_ms)
        self._last_timestamp_ms = timestamp_ms
        if (
            previous_timestamp_ms is not None
            and timestamp_ms - previous_timestamp_ms > self._maximum_observation_gap_ms
        ):
            self._evidence_started_ms = None

        required_posture = "upright" if self._latched else "down"
        if posture != required_posture:
            self._evidence_started_ms = None
            return None
        if self._evidence_started_ms is None:
            self._evidence_started_ms = timestamp_ms

        required_duration_ms = self._rearm_ms if self._latched else self._confirmation_ms
        if timestamp_ms - self._evidence_started_ms < required_duration_ms:
            return None

        self._evidence_started_ms = None
        self._latched = not self._latched
        if not self._latched:
            return None

        return {
            "episode_id": str(uuid4()),
            "signal": "person_down",
            "camera_id": self._camera_id,
            "zone_id": self._zone_id,
            "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": self._source,
        }
