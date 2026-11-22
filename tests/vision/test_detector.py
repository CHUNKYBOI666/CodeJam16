"""Detector tests on synthetic keypoint sequences (no model, no video)."""

from __future__ import annotations

import math
from itertools import count

import pytest

from detector import (
    DetectorConfig,
    PersonDownDetector,
    Posture,
    State,
    classify_posture,
    point_in_polygon,
)

# Floor occupies the bottom half of a 1000x1000 frame.
FLOOR = [(0.0, 500.0), (1000.0, 500.0), (1000.0, 1000.0), (0.0, 1000.0)]
FPS = 30.0


# ---------------------------------------------------------------- fake people


def person(
    torso_deg: float,
    hip=(500.0, 800.0),
    legs: str = "along",
    torso_len: float = 150.0,
    leg_len: float = 250.0,
    torso_conf: float = 0.9,
):
    """17 COCO keypoints for a stick figure.

    torso_deg: angle of the hip->shoulder line from horizontal (90 = standing).
    legs: "along" continues the torso line past the hips (lying flat),
          "down" points legs straight down from the hips (standing / bending over),
          "forward" points legs horizontally (sitting on the floor).
    """
    hx, hy = hip
    a = math.radians(torso_deg)
    sx, sy = hx - torso_len * math.cos(a), hy - torso_len * math.sin(a)  # shoulders up/left
    if legs == "along":
        ax, ay = hx + leg_len * math.cos(a), hy + leg_len * math.sin(a)
    elif legs == "down":
        ax, ay = hx, hy + leg_len
    elif legs == "forward":
        ax, ay = hx + leg_len, hy
    else:
        raise ValueError(legs)
    kx, ky = (hx + ax) / 2, (hy + ay) / 2  # knees
    head = (sx - 40 * math.cos(a), sy - 40 * math.sin(a))
    off = 20.0  # left/right spread perpendicular-ish
    xy = [head] * 5  # nose, eyes, ears
    xy += [(sx, sy - off), (sx, sy + off)]  # shoulders 5, 6
    xy += [(sx, sy), (sx, sy)]  # elbows 7, 8
    xy += [(sx, sy), (sx, sy)]  # wrists 9, 10
    xy += [(hx, hy - off), (hx, hy + off)]  # hips 11, 12
    xy += [(kx, ky), (kx, ky)]  # knees 13, 14
    xy += [(ax, ay), (ax, ay)]  # ankles 15, 16
    conf = [0.9] * 17
    for i in (5, 6, 11, 12):
        conf[i] = torso_conf
    return xy, conf


STANDING = person(90, legs="down", hip=(500, 600))
LYING = person(0, legs="along")
UNKNOWN = (None, None)


class Clock:
    """Feeds frames to a detector at a fixed fps, tracking source time in ms."""

    def __init__(self, det: PersonDownDetector, fps: float = FPS):
        self.det = det
        self.dt_ms = 1000.0 / fps
        self.t_ms = 0.0
        self.alerts = []
        self.last = None

    def hold(self, kp, seconds: float):
        frames = int(round(seconds * 1000.0 / self.dt_ms))
        for _ in range(frames):
            self.last = self.det.update(self.t_ms, *kp)
            if self.last.alert:
                self.alerts.append(self.last.alert)
            self.t_ms += self.dt_ms
        return self

    def skip(self, seconds: float):
        self.t_ms += seconds * 1000.0
        return self


def make(**kw) -> PersonDownDetector:
    ids = count(1)
    return PersonDownDetector(floor_polygon=FLOOR, new_episode_id=lambda: f"ep{next(ids)}", **kw)


# ---------------------------------------------------------------- geometry


def test_point_in_polygon():
    assert point_in_polygon((500, 750), FLOOR)
    assert not point_in_polygon((500, 250), FLOOR)
    tri = [(0, 0), (10, 0), (0, 10)]
    assert point_in_polygon((2, 2), tri)
    assert not point_in_polygon((8, 8), tri)


def test_lying_is_down():
    r = classify_posture(*LYING, FLOOR)
    assert r.posture is Posture.DOWN
    assert r.torso.angle_deg == pytest.approx(0, abs=1e-6)
    assert r.bbox_aspect > 1


def test_standing_is_upright():
    assert classify_posture(*STANDING, FLOOR).posture is Posture.UPRIGHT


def test_weak_torso_keypoint_is_unknown():
    assert classify_posture(*person(0, torso_conf=0.4), FLOOR).posture is Posture.UNKNOWN


def test_flat_torso_but_tall_bbox_is_not_down():
    # Bent over at the waist with torso near horizontal, legs straight down.
    xy, conf = person(15, legs="down", leg_len=400)
    r = classify_posture(xy, conf, FLOOR)
    assert r.torso.angle_deg <= 30
    assert r.bbox_aspect < 1
    assert r.posture is not Posture.DOWN


def test_flat_torso_outside_floor_is_not_down():
    xy, conf = person(0, hip=(500, 300))  # e.g. lying on a couch / reflection above the floor line
    assert classify_posture(xy, conf, FLOOR).posture is not Posture.DOWN


def test_one_midpoint_outside_floor_is_not_down():
    # Hips on the floor, shoulders hanging above the floor line (torso ~20 deg).
    xy, conf = person(20, hip=(500, 540), torso_len=200)
    r = classify_posture(xy, conf, FLOOR)
    assert r.torso.angle_deg <= 30
    assert not r.in_floor
    assert r.posture is not Posture.DOWN


def test_no_floor_polygon_means_whole_frame():
    xy, conf = person(0, hip=(500, 300))
    assert classify_posture(xy, conf, None).posture is Posture.DOWN


# ---------------------------------------------------------------- state machine


def test_sustained_down_alerts_once_after_two_seconds():
    c = Clock(make()).hold(STANDING, 2).hold(LYING, 10)
    assert len(c.alerts) == 1
    a = c.alerts[0]
    assert a.episode_id == "ep1"
    assert a.signal == "possible_person_down"
    assert (a.detected_at_ms - a.down_started_ms) / 1000 == pytest.approx(2.0, abs=1 / FPS)
    assert a.down_started_ms == pytest.approx(2000, abs=1000 / FPS)
    assert c.last.state is State.ALERTED


def test_brief_down_does_not_alert():
    c = Clock(make()).hold(STANDING, 1).hold(LYING, 1.5).hold(STANDING, 3)
    assert c.alerts == []
    assert c.last.state is State.UPRIGHT


def test_deep_bend_at_40_degrees_held_three_seconds_does_not_alert():
    bend = person(40, legs="down", hip=(500, 600))
    c = Clock(make()).hold(STANDING, 1).hold(bend, 3).hold(STANDING, 1)
    assert c.alerts == []
    assert classify_posture(*bend, FLOOR).posture is Posture.OTHER


def test_deep_bend_at_40_degrees_with_wide_bbox_still_does_not_alert():
    # Even if the box were wide and on the floor, 40 deg fails the torso rule.
    bend = person(40, legs="forward")
    assert classify_posture(*bend, FLOOR).bbox_aspect > 1
    c = Clock(make()).hold(bend, 3)
    assert c.alerts == []


def test_flat_bend_with_legs_down_held_three_seconds_does_not_alert():
    bend = person(15, legs="down", leg_len=400)
    assert Clock(make()).hold(bend, 3).alerts == []


def test_sitting_on_floor_does_not_alert():
    sit = person(80, legs="forward", hip=(500, 800))
    assert Clock(make()).hold(sit, 5).alerts == []


def test_crouch_does_not_alert():
    crouch = person(65, legs="down", hip=(500, 750), leg_len=120)
    assert Clock(make()).hold(crouch, 5).alerts == []


def test_already_down_at_startup_alerts():
    c = Clock(make()).hold(LYING, 3)
    assert len(c.alerts) == 1
    assert c.alerts[0].detected_at_ms == pytest.approx(2000, abs=1000 / FPS)


def test_unknown_breaks_pending_timer():
    c = Clock(make()).hold(LYING, 1.5).hold(UNKNOWN, 0.2).hold(LYING, 1.5)
    assert c.alerts == []
    c.hold(LYING, 0.6)  # now 2.1 s continuous since the UNKNOWN
    assert len(c.alerts) == 1
    a = c.alerts[0]
    assert (a.detected_at_ms - a.down_started_ms) / 1000 == pytest.approx(2.0, abs=1 / FPS)


def test_frame_gap_breaks_pending_timer():
    c = Clock(make()).hold(LYING, 1.5).skip(1.0).hold(LYING, 1.5)
    assert c.alerts == []


def test_unknown_never_clears_alerted():
    c = Clock(make()).hold(LYING, 3).hold(UNKNOWN, 10).hold(LYING, 3)
    assert len(c.alerts) == 1
    assert c.last.state is State.ALERTED


def test_latched_through_staff_time_on_floor_and_short_recovery():
    c = Clock(make()).hold(LYING, 3).hold(LYING, 20)  # still down long after alert
    c.hold(STANDING, 1.0).hold(LYING, 5)  # short stand, back down: same episode
    assert len(c.alerts) == 1
    assert c.last.state is State.ALERTED
    assert c.last.episode_id == "ep1"


def test_half_sit_up_does_not_rearm():
    half = person(45, legs="forward")  # propped up on the floor, OTHER
    c = Clock(make()).hold(LYING, 3).hold(half, 5).hold(LYING, 3)
    assert len(c.alerts) == 1


def test_unknown_breaks_rearm_timer():
    c = Clock(make()).hold(LYING, 3)
    c.hold(STANDING, 1.5).hold(UNKNOWN, 0.1).hold(STANDING, 1.5)
    assert c.last.state is State.ALERTED
    c.hold(STANDING, 0.6)
    assert c.last.state is State.UPRIGHT


def test_rearm_after_two_seconds_upright_then_second_episode():
    c = Clock(make()).hold(LYING, 3).hold(STANDING, 2.5).hold(LYING, 3)
    assert [a.episode_id for a in c.alerts] == ["ep1", "ep2"]


def test_uses_timestamps_not_frame_counts():
    for fps in (5, 10, 30, 60):
        c = Clock(make(), fps=fps).hold(LYING, 1.9)
        assert c.alerts == [], fps
        c.hold(LYING, 0.4)
        assert len(c.alerts) == 1, fps


def test_reset_clears_latched_state():
    det = make()
    Clock(det).hold(LYING, 3)
    assert det.state is State.ALERTED
    det.reset()
    assert det.state is State.UPRIGHT and det.episode_id is None


def test_status_progress_values():
    det = make()
    c = Clock(det).hold(LYING, 1.0)
    assert c.last.state is State.PENDING
    assert c.last.down_seconds == pytest.approx(1.0 - 1 / FPS, abs=1e-6)
    c.hold(LYING, 1.5).hold(STANDING, 1.0)
    assert c.last.state is State.ALERTED
    assert c.last.rearm_seconds == pytest.approx(1.0 - 1 / FPS, abs=1e-6)


def test_config_thresholds_are_respected():
    cfg = DetectorConfig(confirm_down_seconds=0.5)
    c = Clock(make(cfg=cfg)).hold(LYING, 0.6)
    assert len(c.alerts) == 1
