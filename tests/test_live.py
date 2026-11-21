from contextlib import contextmanager, redirect_stderr
from io import StringIO
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from codejam16.capture import CapturedFrame, CaptureOpenError, LiveFeedLost
from codejam16.live import DetectorWorker, FeedState
from codejam16.store import IncidentStore
from codejam16.venue import Camera, Venue, Zone
from test_detector import DOWN, FLOOR_BOX, SITTING, UPRIGHT


VENUE = Venue(
    zones={"east-bar": Zone("east-bar", "East bar", "Beside the side stairs", 241, 118)},
    cameras={"demo-camera": Camera("demo-camera", "east-bar", FLOOR_BOX, {})},
)


def samples(frame, start, end):
    return [CapturedFrame(frame, timestamp) for timestamp in range(start, end, 500)]


def opener(frames, *, then_raise=None, calls=None):
    @contextmanager
    def open_source(*args, **kwargs):
        if calls is not None:
            calls.append(args)
        def generate():
            yield from frames
            if then_raise is not None:
                raise then_raise
        yield generate()
    return open_source


def failing_opener(error):
    @contextmanager
    def open_source(*args, **kwargs):
        raise error
        yield
    return open_source


def predictor():
    return lambda frame: frame.results


def ticking_clock(step=0.25):
    now = [0.0]
    def clock():
        now[0] += step
        return now[0]
    return clock


class DetectorWorkerTests(unittest.TestCase):
    def worker(self, deliver, feed, **kwargs):
        options = dict(venue=VENUE, camera_id="demo-camera", deliver=deliver, feed=feed,
                       load_predictor=predictor, sleep=lambda seconds: None)
        options.update(kwargs)
        return DetectorWorker(**options)

    def run_worker(self, worker):
        with redirect_stderr(StringIO()):
            worker.run()

    def test_replay_creates_one_enriched_check(self):
        events, feed = [], FeedState()
        self.run_worker(self.worker(events.append, feed, replay_path=Path("clip.mov"),
                                    open_replay=opener(samples(DOWN, 0, 3000))))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["zone_id"], "east-bar")
        self.assertEqual(events[0]["zone_name"], "East bar")
        self.assertEqual(events[0]["landmark"], "Beside the side stairs")
        self.assertEqual(feed.snapshot()["status"], "finished")
        self.assertEqual(feed.snapshot()["label"], "Rehearsal clip ended")

    def test_pre_check_loss_uses_fresh_pipeline(self):
        events, feed = [], FeedState()
        self.run_worker(self.worker(
            events.append, feed, camera_index=1, fallback_clip=Path("clip.mov"),
            open_camera=opener(samples(DOWN, 0, 1500), then_raise=LiveFeedLost("gone")),
            open_replay=opener(samples(DOWN, 1500, 2500))))
        self.assertEqual(events, [])
        self.assertEqual(feed.snapshot()["status"], "finished")

    def test_pre_check_loss_fallback_detects(self):
        events, feed = [], FeedState()
        self.run_worker(self.worker(
            events.append, feed, camera_index=1, fallback_clip=Path("clip.mov"),
            open_camera=opener(samples(SITTING, 0, 3000), then_raise=LiveFeedLost("gone")),
            open_replay=opener(samples(DOWN, 0, 3000))))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "replay")

    def test_post_check_loss_goes_offline(self):
        events, feed, replay_calls = [], FeedState(), []
        self.run_worker(self.worker(
            events.append, feed, camera_index=1, fallback_clip=Path("clip.mov"),
            open_camera=opener(samples(DOWN, 0, 3000), then_raise=LiveFeedLost("gone")),
            open_replay=opener(samples(DOWN, 0, 3000), calls=replay_calls)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "live")
        self.assertEqual(replay_calls, [])
        self.assertEqual(feed.snapshot()["status"], "offline")
        self.assertEqual(feed.snapshot()["label"], "Camera offline")

    def test_missing_fallback_goes_offline(self):
        events, feed = [], FeedState()
        self.run_worker(self.worker(
            events.append, feed, camera_index=1, fallback_clip=Path("missing.mov"),
            open_camera=failing_opener(CaptureOpenError("no camera")),
            open_replay=failing_opener(CaptureOpenError("no clip"))))
        self.assertEqual(events, [])
        self.assertEqual(feed.snapshot()["status"], "offline")

    def test_model_load_failure_marks_unavailable(self):
        feed, camera_calls = FeedState(), []

        def broken():
            raise RuntimeError("weights missing")

        self.run_worker(self.worker(lambda event: None, feed, camera_index=1,
                                    load_predictor=broken,
                                    open_camera=opener([], calls=camera_calls)))
        self.assertEqual(feed.snapshot()["status"], "detector_unavailable")
        self.assertEqual(feed.snapshot()["label"], "Detector unavailable")
        self.assertEqual(camera_calls, [])

    def test_delivery_retry_creates_one_check(self):
        with TemporaryDirectory() as directory:
            store = IncidentStore(str(Path(directory) / "staff.sqlite3"))
            attempts = []

            def flaky(event):
                attempts.append(event["episode_id"])
                if len(attempts) == 1:
                    raise sqlite3.OperationalError("database is locked")
                return store.create_incident(event)

            feed = FeedState()
            self.run_worker(self.worker(flaky, feed, replay_path=Path("clip.mov"),
                                        open_replay=opener(samples(DOWN, 0, 3000))))
            self.assertEqual(store.count(), 1)
            self.assertEqual(len(set(attempts)), 1)
            self.assertIsNotNone(store.get_incident(attempts[0]))
            self.assertEqual(feed.snapshot()["status"], "finished")

    def test_delivery_gives_up_after_attempts(self):
        feed = FeedState()

        def broken(event):
            raise sqlite3.OperationalError("disk I/O error")

        self.run_worker(self.worker(broken, feed, replay_path=Path("clip.mov"),
                                    delivery_attempts=3,
                                    open_replay=opener(samples(DOWN, 0, 3000))))
        self.assertEqual(feed.snapshot()["status"], "offline")
        self.assertEqual(feed.snapshot()["detail"], "Could not save check")

    def test_fps_reported(self):
        feed = FeedState()
        self.run_worker(self.worker(lambda event: None, feed, replay_path=Path("clip.mov"),
                                    clock=ticking_clock(),
                                    open_replay=opener(samples(SITTING, 0, 5000))))
        snapshot = feed.snapshot()
        self.assertGreater(snapshot["fps"], 0)
        self.assertTrue(snapshot["last_frame_at"].endswith("Z"))


class StaffResolveTests(unittest.TestCase):
    ADA = {"id": "responder-ada", "name": "Ada Chen", "role": "responder"}

    def run_with_staff(self, after_resolve):
        with TemporaryDirectory() as directory:
            store = IncidentStore(str(Path(directory) / "staff.sqlite3"))

            def frames():
                yield from samples(DOWN, 0, 3000)
                (check_id,) = [check["id"] for check in store.snapshot(self.ADA, {})["checks"]]
                for action in ("accept", "arrive"):
                    store.transition(check_id, self.ADA, action)
                store.transition(check_id, self.ADA, "resolve", "Confirmed person down")
                yield from after_resolve

            @contextmanager
            def open_replay(*args, **kwargs):
                yield frames()

            worker = DetectorWorker(venue=VENUE, camera_id="demo-camera", deliver=store.create_incident,
                                    feed=FeedState(), load_predictor=predictor, replay_path=Path("clip.mov"),
                                    open_replay=open_replay, sleep=lambda seconds: None)
            with redirect_stderr(StringIO()):
                worker.run()
            return store.count()

    def test_still_down_after_resolve_creates_no_new_check(self):
        self.assertEqual(self.run_with_staff(samples(DOWN, 3000, 8000)), 1)

    def test_upright_then_down_again_creates_second_check(self):
        self.assertEqual(self.run_with_staff(samples(UPRIGHT, 3000, 6000) + samples(DOWN, 6000, 9000)), 2)


class FeedStateTests(unittest.TestCase):
    def test_snapshot_has_required_fields_and_is_a_copy(self):
        feed = FeedState()
        snapshot = feed.snapshot()
        self.assertEqual(set(snapshot), {"status", "label", "detail", "source", "fps", "last_frame_at"})
        self.assertEqual(snapshot["status"], "rehearsal")
        snapshot["status"] = "changed"
        self.assertEqual(feed.snapshot()["status"], "rehearsal")


if __name__ == "__main__":
    unittest.main()
