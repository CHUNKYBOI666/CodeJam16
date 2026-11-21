from contextlib import redirect_stderr
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from codejam16.capture import CapturedFrame, CaptureOpenError, LiveFeedLost
from codejam16.pipeline import DeliveryError, PersonDownPipeline
from codejam16 import vision_cli
from test_detector import DOWN, UPRIGHT, SITTING, FLOOR_BOX


def samples(frame, start, end):
    return [CapturedFrame(frame, timestamp) for timestamp in range(start, end, 500)]


def pipeline():
    return PersonDownPipeline(lambda frame: frame.results, camera_id="demo-camera",
                              zone_id="demo-area", floor_box=FLOOR_BOX)


class PipelineTests(unittest.TestCase):
    def test_live_episodes_and_delivery_contract(self):
        worker = pipeline()
        events = []
        frames = (
            samples(SITTING, 0, 3000) + samples(DOWN, 3000, 6000)
            + samples(None, 6000, 7000) + samples(DOWN, 7000, 10000)
            + samples(UPRIGHT, 10000, 13000) + samples(DOWN, 13000, 16000)
        )
        with redirect_stderr(StringIO()):
            worker.run(frames, source="live", deliver=events.append)
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["episode_id"], events[1]["episode_id"])
        fixture = json.loads((Path(__file__).parents[1] / "fixtures/person-down.json").read_text())
        for event in events:
            self.assertEqual(set(event), set(fixture))
            self.assertEqual(event["source"], "live")
            self.assertEqual(event["signal"], "person_down")
            self.assertTrue(event["detected_at"].endswith("Z"))
            self.assertEqual(datetime.fromisoformat(event["detected_at"]).utcoffset(), timedelta(0))

    def test_failed_delivery_keeps_event_for_retry(self):
        worker = pipeline()
        received = []

        def fail(event):
            received.append(event)
            raise OSError("temporary error")

        with redirect_stderr(StringIO()), self.assertRaises(DeliveryError):
            worker.run(samples(DOWN, 0, 3000), source="replay", deliver=fail)
        self.assertEqual(worker.feed_state, "delivery_error")
        self.assertEqual(worker.pending_event, received[0])
        worker.deliver_pending(received.append)
        self.assertEqual(received[0], received[1])
        self.assertIsNone(worker.pending_event)

    def test_pre_alert_camera_failure_restarts_replay_observation(self):
        worker = pipeline()

        def interrupted():
            yield from samples(DOWN, 0, 2000)
            raise LiveFeedLost("stalled")

        events = []
        with redirect_stderr(StringIO()):
            with self.assertRaises(LiveFeedLost):
                worker.run(interrupted(), source="live", deliver=events.append)
            self.assertEqual(worker.feed_state, "offline")
            worker.run(samples(DOWN, 0, 3000), source="replay", deliver=events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "replay")

    def test_confirmed_episode_blocks_replay_switch_and_survives_loss(self):
        worker = pipeline()
        events = []

        def interrupted():
            yield from samples(DOWN, 0, 3000)
            raise LiveFeedLost("disconnected")

        with redirect_stderr(StringIO()):
            with self.assertRaises(LiveFeedLost):
                worker.run(interrupted(), source="live", deliver=events.append)
            with self.assertRaises(ValueError):
                worker.run(samples(DOWN, 0, 3000), source="replay", deliver=events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(worker.feed_state, "offline")

    def test_bad_clock_surfaces_detector_error(self):
        worker = pipeline()
        with redirect_stderr(StringIO()), self.assertRaises(ValueError):
            worker.run([CapturedFrame(DOWN, 10), CapturedFrame(DOWN, 10)],
                       source="live", deliver=lambda event: None)
        self.assertEqual(worker.feed_state, "detector_error")

    def test_failed_replay_open_reports_offline(self):
        worker = pipeline()
        with self.assertRaises(CaptureOpenError):
            worker.run_replay("/nonexistent/staged-clip.mov", lambda event: None)
        self.assertEqual(worker.feed_state, "offline")

    def test_delivery_callback_cannot_change_pending_event(self):
        worker = pipeline()
        def mutate_and_fail(event):
            event["episode_id"] = "changed"
            raise OSError("temporary")
        with redirect_stderr(StringIO()), self.assertRaises(DeliveryError):
            worker.run(samples(DOWN, 0, 3000), source="live", deliver=mutate_and_fail)
        self.assertNotEqual(worker.pending_event["episode_id"], "changed")

    def test_cli_switches_to_replay_only_before_an_alert(self):
        @contextmanager
        def live(*args, **kwargs):
            def interrupted():
                yield from samples(DOWN, 0, 1500)
                raise LiveFeedLost("disconnected")
            yield interrupted()

        @contextmanager
        def replay(*args, **kwargs):
            yield iter(samples(DOWN, 0, 3000))

        stdout, stderr = StringIO(), StringIO()
        with (
            patch("codejam16.pipeline.open_camera", live),
            patch("codejam16.pipeline.open_replay", replay),
            patch.object(vision_cli, "_load_pose_predictor",
                         return_value=lambda frame: frame.results),
            patch.object(Path, "is_file", return_value=True),
            redirect_stderr(stderr),
            unittest.mock.patch("sys.stdout", stdout),
        ):
            status = vision_cli.main(["--camera-index", "1", "--fallback-clip", "staged.mov",
                                      "--floor-box", *map(str, FLOOR_BOX)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["source"], "replay")
        self.assertIn("Live camera offline", stderr.getvalue())
        self.assertIn("Switching to labeled replay", stderr.getvalue())
