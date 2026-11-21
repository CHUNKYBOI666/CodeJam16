from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codejam16 import vision_cli
from codejam16 import capture as capture_module
from codejam16.detector import PersonDownDetector


FLOOR_BOX = (0.10, 0.65, 0.95, 0.98)
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "person-down.json"
FRAME_SHAPE = (500, 1000, 3)


def torso(shoulders, hips, *, confidence=0.95):
    coordinates = [[0, 0] for _ in range(17)]
    confidences = [0.0] * 17
    for first_joint, (x, y) in ((5, shoulders), (11, hips)):
        coordinates[first_joint:first_joint + 2] = [[x - 10, y], [x + 10, y]]
        confidences[first_joint:first_joint + 2] = [confidence, confidence]
    return coordinates, confidences


def frame_with(*people):
    coordinates = [person[0] for person in people]
    confidences = [person[1] for person in people]
    keypoints = SimpleNamespace(xy=coordinates, conf=confidences)
    result = SimpleNamespace(keypoints=keypoints)
    return SimpleNamespace(shape=FRAME_SHAPE, size=500 * 1000 * 3, results=[result])


DOWN = frame_with(torso((300, 370), (400, 415)))
UPRIGHT = frame_with(torso((400, 160), (410, 270)))
SITTING = frame_with(torso((420, 240), (430, 365)))
BENDING = frame_with(torso((300, 210), (400, 220)))
CROUCHING = frame_with(torso((360, 295), (415, 370)))


class PersonDownDetectorTests(unittest.TestCase):
    def make_detector(self, **options):
        return PersonDownDetector(
            lambda frame_bgr: frame_bgr.results, floor_box=FLOOR_BOX, **options
        )

    def assert_no_events(self, detector, frame, timestamps_ms):
        for timestamp_ms in timestamps_ms:
            self.assertIsNone(detector.update(frame, timestamp_ms), f"{timestamp_ms} ms")

    def confirm_episode(self, detector, start_ms=0):
        self.assert_no_events(
            detector, DOWN, range(start_ms, start_ms + 2000, 500)
        )
        event = detector.update(DOWN, start_ms + 2000)
        self.assertIsNotNone(event)
        return event

    def test_ordinary_activity_has_no_events(self):
        detector = self.make_detector()
        walking_frames = [
            frame_with(torso((x, 160), (x + 10, 270)))
            for x in (200, 230, 260, 290, 320, 350)
        ]
        for frame, timestamp_ms in zip(walking_frames, range(0, 3000, 500)):
            self.assert_no_events(detector, frame, [timestamp_ms])
        self.assert_no_events(detector, SITTING, range(3000, 6000, 500))
        self.assert_no_events(detector, BENDING, range(6000, 9000, 500))
        self.assert_no_events(detector, CROUCHING, [9000, 9250, 9500])
        self.assert_no_events(detector, UPRIGHT, [9750, 10000])

    def test_both_midpoints_must_be_inside_floor_box(self):
        for shoulders, hips in (
            ((75, 370), (180, 380)),
            ((850, 370), (980, 380)),
            ((300, 310), (400, 340)),
        ):
            with self.subTest(shoulders=shoulders, hips=hips):
                detector = self.make_detector()
                frame = frame_with(torso(shoulders, hips))
                self.assert_no_events(detector, frame, range(0, 3000, 500))
                self.assertIsNone(detector.last_unknown_reason)

    def test_confirms_already_down_using_elapsed_time_at_different_cadences(self):
        for step_ms in (125, 400, 500):
            with self.subTest(step_ms=step_ms):
                detector = self.make_detector()
                self.assert_no_events(detector, DOWN, range(0, 2000, step_ms))
                self.assert_no_events(detector, DOWN, [1999])
                self.assertIsNotNone(detector.update(DOWN, 2000))
                self.assert_no_events(detector, DOWN, range(2000 + step_ms, 5000, step_ms))

    def test_confirms_on_first_sample_after_threshold_following_ordinary_activity(self):
        detector = self.make_detector()
        self.assert_no_events(detector, SITTING, [0, 500, 1000])
        self.assert_no_events(detector, DOWN, range(1250, 3250, 300))
        self.assertIsNotNone(detector.update(DOWN, 3350))

    def test_ordinary_activity_interrupts_pending_confirmation(self):
        detector = self.make_detector()
        self.assert_no_events(detector, DOWN, [0, 500, 1000, 1500])
        self.assert_no_events(detector, SITTING, [1750])
        self.confirm_episode(detector, 2000)

    def test_unknown_evidence_interrupts_pending_confirmation(self):
        cases = {
            "missing frame": None,
            "empty results": SimpleNamespace(shape=FRAME_SHAPE, results=[]),
            "no people": frame_with(),
            "low confidence": frame_with(torso((300, 370), (400, 415), confidence=0.39)),
            "nonfinite joint": frame_with(torso((float("nan"), 370), (400, 415))),
            "degenerate torso": frame_with(torso((300, 370), (300, 370))),
            "two people": frame_with(torso((300, 370), (400, 415)), torso((600, 160), (600, 270))),
        }
        for description, unknown in cases.items():
            with self.subTest(description=description):
                detector = self.make_detector()
                self.assert_no_events(detector, DOWN, [0, 500, 1000, 1500])
                self.assert_no_events(detector, unknown, [1750])
                self.assertIsInstance(detector.last_unknown_reason, str)
                self.confirm_episode(detector, 2000)
                self.assertIsNone(detector.last_unknown_reason)

    def test_large_timestamp_gap_restarts_pending_confirmation(self):
        detector = self.make_detector()
        self.assert_no_events(detector, DOWN, [0, 500, 1000, 1500])
        self.confirm_episode(detector, 2001)

    def test_latched_episode_survives_loss_reappearance_and_gap(self):
        detector = self.make_detector()
        event = self.confirm_episode(detector)
        original_id = event["episode_id"]
        self.assert_no_events(detector, DOWN, [2500])
        self.assert_no_events(detector, None, [3000, 3500, 4000])
        self.assert_no_events(detector, DOWN, range(4500, 7500, 500))
        self.assert_no_events(detector, DOWN, range(9000, 12000, 500))
        self.assertEqual(event["episode_id"], original_id)

    def test_short_or_interrupted_recovery_keeps_episode_latched(self):
        cases = {
            "down": DOWN,
            "sitting": SITTING,
            "unknown": None,
            "timestamp gap": UPRIGHT,
        }
        for description, interruption in cases.items():
            with self.subTest(description=description):
                detector = self.make_detector()
                first_event = self.confirm_episode(detector)
                self.assert_no_events(detector, UPRIGHT, [2500, 3000, 3500, 4000])
                if description == "timestamp gap":
                    self.assert_no_events(detector, interruption, [4501, 5001, 5501])
                    next_upright_ms = 6001
                else:
                    self.assert_no_events(detector, interruption, [4250])
                    self.assert_no_events(detector, UPRIGHT, [4500, 5000, 5500])
                    next_upright_ms = 6000
                self.assert_no_events(
                    detector, DOWN, range(next_upright_ms, next_upright_ms + 3000, 500)
                )
                recovery_start_ms = next_upright_ms + 3000
                self.assert_no_events(
                    detector, UPRIGHT, range(recovery_start_ms, recovery_start_ms + 2001, 500)
                )
                second_event = self.confirm_episode(detector, recovery_start_ms + 2500)
                self.assertNotEqual(first_event["episode_id"], second_event["episode_id"])

    def test_invalid_timestamps_do_not_change_pending_confirmation(self):
        detector = self.make_detector()
        self.assert_no_events(detector, DOWN, [0, 500, 1000, 1500])
        for invalid_ms in (1500, 1499, -1, float("nan"), float("inf"), None, "2000"):
            with self.subTest(invalid_ms=invalid_ms):
                with self.assertRaises(ValueError):
                    detector.update(None, invalid_ms)
        self.assertIsNotNone(detector.update(DOWN, 2000))

    def test_event_matches_fixture_and_contains_confirmation_time(self):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        detector = self.make_detector()
        before = datetime.now(timezone.utc)
        event = self.confirm_episode(detector)
        after = datetime.now(timezone.utc)

        self.assertEqual(set(event), set(fixture))
        for field, value in fixture.items():
            self.assertIsInstance(event[field], type(value))
        for field in ("signal", "camera_id", "zone_id", "source"):
            self.assertEqual(event[field], fixture[field])
        self.assertTrue(event["episode_id"].strip())
        self.assertTrue(event["detected_at"].endswith("Z"))
        detected_at = datetime.fromisoformat(event["detected_at"])
        self.assertEqual(detected_at.utcoffset(), timedelta(0))
        self.assertLessEqual(before - timedelta(seconds=1), detected_at)
        self.assertLessEqual(detected_at, after)
        self.assertEqual(json.loads(json.dumps(event)), event)

    def test_event_uses_configured_camera_zone_and_source(self):
        detector = self.make_detector(camera_id="camera-3", zone_id="lower-level", source="live")
        event = self.confirm_episode(detector)
        self.assertEqual(event["camera_id"], "camera-3")
        self.assertEqual(event["zone_id"], "lower-level")
        self.assertEqual(event["source"], "live")


class FakeCapture:
    def __init__(self, frames, *, reported_frames=None):
        self.reported_frames = len(frames) if reported_frames is None else reported_frames
        self.frames = iter(frames)
        self.timestamp_ms = 0
        self.released = False

    def isOpened(self):
        return True

    def getBackendName(self):
        return "test decoder"

    def get(self, prop):
        if prop == "position":
            return self.timestamp_ms
        if prop == "count":
            return self.reported_frames
        raise AssertionError(f"Unexpected capture property {prop}")

    def read(self):
        frame = next(self.frames, None)
        if frame is None:
            return False, None
        self.timestamp_ms, frame_bgr = frame
        return True, frame_bgr

    def release(self):
        self.released = True


class ReplayCliTests(unittest.TestCase):
    def setUp(self):
        with_dir = self.enterContext(TemporaryDirectory())
        self.clip = Path(with_dir) / "staged clip.mp4"
        self.weights = Path(with_dir) / "yolo26n-pose.pt"
        self.clip.touch()
        self.weights.touch()

    def run_cli(self, capture, load_pose_predictor, extra_args=(), *, stdout=None):
        cv2_mock = SimpleNamespace(
            VideoCapture=lambda _path: capture,
            CAP_PROP_POS_MSEC="position",
            CAP_PROP_FRAME_COUNT="count",
            error=type("CaptureError", (Exception,), {}),
        )
        if stdout is None:
            stdout = StringIO()
        stderr = StringIO()
        argv = [
            str(self.clip),
            "--weights",
            str(self.weights),
            "--floor-box",
            *map(str, FLOOR_BOX),
            *extra_args,
        ]
        with (
            patch.object(capture_module, "cv2", cv2_mock),
            patch.object(capture_module, "_is_bgr", side_effect=lambda frame:
                         frame is not None and len(frame.shape) == 3 and frame.shape[2] == 3),
            patch.object(vision_cli, "_load_pose_predictor", load_pose_predictor),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = vision_cli.main(argv)
        return status, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def load_pose_predictor(_weights):
        print("loading model")

        def predict_pose(frame_bgr):
            print("running model")
            return frame_bgr.results

        return predict_pose

    def test_stdout_contains_only_the_fixture_event_and_reports_eof(self):
        capture = FakeCapture([(timestamp_ms, DOWN) for timestamp_ms in range(0, 3000, 500)])
        status, stdout, stderr = self.run_cli(
            capture, self.load_pose_predictor, ["--camera-id", "east-demo"]
        )

        self.assertEqual(status, 0)
        self.assertTrue(capture.released)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(set(event), set(json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))))
        self.assertEqual(event["camera_id"], "east-demo")
        self.assertEqual(event["signal"], "person_down")
        self.assertIn("loading model", stderr)
        self.assertIn("running model", stderr)
        self.assertIn(f"Confirmed {event['episode_id']} at 2000.0 ms", stderr)
        self.assertIn("End of replay", stderr)

    def test_eof_before_confirmation_emits_no_event(self):
        capture = FakeCapture([(timestamp_ms, DOWN) for timestamp_ms in range(0, 2000, 500)])
        status, stdout, stderr = self.run_cli(capture, self.load_pose_predictor)

        self.assertEqual((status, stdout), (0, ""))
        self.assertTrue(capture.released)
        self.assertIn("frames=4 events=0", stderr)
        self.assertIn("End of replay", stderr)

    def test_bad_clock_exits_and_does_not_finish_pending_episode(self):
        capture = FakeCapture([(timestamp_ms, DOWN) for timestamp_ms in (0, 500, 1000, 1500, 1500)])
        status, stdout, stderr = self.run_cli(capture, self.load_pose_predictor)

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("unusable media timestamp at frame 5", stderr)
        self.assertNotIn("End of replay", stderr)

    def test_early_decoder_stop_exits_with_frame_counts(self):
        capture = FakeCapture([(0, DOWN)], reported_frames=10)
        status, stdout, stderr = self.run_cli(capture, self.load_pose_predictor)

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("decoder stopped after 1 of 10 reported frames", stderr)

    def test_empty_recording_exits_and_releases_capture(self):
        capture = FakeCapture([])
        status, stdout, stderr = self.run_cli(capture, self.load_pose_predictor)

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("no readable frames", stderr)

    def test_non_bgr_input_fails_before_pose_processing(self):
        capture = FakeCapture([(0, SimpleNamespace(shape=(500, 1000), size=500_000))])
        status, stdout, stderr = self.run_cli(capture, self.load_pose_predictor)

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("invalid BGR image at frame 1", stderr)
        self.assertNotIn("running model", stderr)

    def test_inference_error_exits_and_releases_capture(self):
        def load_failing_model(_weights):
            def predict_pose(_frame):
                raise RuntimeError("test inference error")

            return predict_pose

        capture = FakeCapture([(0, DOWN)])
        status, stdout, stderr = self.run_cli(capture, load_failing_model)

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("Vision pipeline failed", stderr)
        self.assertIn("test inference error", stderr)

    def test_missing_weights_fails_before_model_load(self):
        def load_unexpected_model(_weights):
            self.fail("Missing weights should prevent model load")

        self.weights.unlink()
        status, stdout, stderr = self.run_cli(FakeCapture([]), load_unexpected_model)

        self.assertEqual((status, stdout), (1, ""))
        self.assertIn("pose checkpoint is not a local file", stderr)

    def test_broken_output_pipe_reports_failure_and_releases_capture(self):
        class BrokenOutput(StringIO):
            def write(self, _text):
                raise BrokenPipeError("output closed")

        capture = FakeCapture([(timestamp_ms, DOWN) for timestamp_ms in range(0, 3000, 500)])
        status, stdout, stderr = self.run_cli(
            capture, self.load_pose_predictor, stdout=BrokenOutput()
        )

        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(capture.released)
        self.assertIn("delivery failed for episode", stderr)
        self.assertNotIn("Confirmed", stderr)

    def test_interrupt_releases_capture(self):
        def load_interrupted_model(_weights):
            def predict_pose(_frame):
                raise KeyboardInterrupt

            return predict_pose

        capture = FakeCapture([(0, DOWN)])
        status, stdout, stderr = self.run_cli(capture, load_interrupted_model)

        self.assertEqual((status, stdout), (130, ""))
        self.assertTrue(capture.released)
        self.assertIn("Vision pipeline interrupted", stderr)
