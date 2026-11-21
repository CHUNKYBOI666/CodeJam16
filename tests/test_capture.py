"""Capture adapter tests that do not require a camera."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

import cv2
import numpy as np

from codejam16 import capture
from codejam16.capture_cli import main


def image(value):
    return np.full((2, 3, 3), value, dtype=np.uint8)


class FakeVideo:
    def __init__(self, frames=(), *, opened=True, reported_frames=0):
        self.frames = iter(frames)
        self.opened = opened
        self.reported_frames = reported_frames
        self.timestamp_ms = 0
        self.released = Event()

    def isOpened(self):
        return self.opened

    def getBackendName(self):
        return "FAKE"

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return self.timestamp_ms
        return self.reported_frames

    def read(self):
        next_frame = next(self.frames, None)
        if next_frame is None:
            return False, None
        value, self.timestamp_ms = next_frame
        return True, image(value)

    def release(self):
        self.released.set()


class ReplayTests(TestCase):
    clip = Path(__file__)

    def test_media_time_decode_order_and_eof(self):
        video = FakeVideo([(2, 80), (5, 126.5), (9, 180)], reported_frames=3)
        with patch.object(cv2, "VideoCapture", return_value=video):
            with capture.open_replay(self.clip) as frames:
                samples = list(frames)
                self.assertEqual(list(frames), [])
        self.assertEqual([sample.timestamp_ms for sample in samples], [0, 46.5, 100])
        self.assertEqual([int(sample.frame_bgr[0, 0, 0]) for sample in samples], [2, 5, 9])
        self.assertEqual(samples[0].frame_bgr.shape, (2, 3, 3))
        self.assertTrue(video.released.is_set())

    def test_constant_rate_ignores_missing_backend_timestamps(self):
        video = FakeVideo([(2, 0), (3, 0), (4, 0)])
        with patch.object(cv2, "VideoCapture", return_value=video):
            with capture.open_replay(self.clip, constant_fps=25) as frames:
                self.assertEqual([frame.timestamp_ms for frame in frames], [0, 40, 80])

    def test_repeated_or_invalid_media_time_is_an_error(self):
        for timestamp_ms in (0, -1, float("nan")):
            with self.subTest(timestamp_ms=timestamp_ms):
                video = FakeVideo([(2, 0), (3, timestamp_ms)])
                with patch.object(cv2, "VideoCapture", return_value=video):
                    with self.assertRaises(capture.CaptureReadError):
                        with capture.open_replay(self.clip) as frames:
                            list(frames)
                self.assertTrue(video.released.is_set())

    def test_failed_open_and_early_decoder_stop_are_distinct(self):
        video = FakeVideo(opened=False)
        with patch.object(cv2, "VideoCapture", return_value=video):
            with self.assertRaises(capture.CaptureOpenError):
                with capture.open_replay(self.clip):
                    pass
        self.assertTrue(video.released.is_set())

        video = FakeVideo([(2, 0)], reported_frames=2)
        with patch.object(cv2, "VideoCapture", return_value=video):
            with capture.open_replay(self.clip) as frames:
                next(frames)
                with self.assertRaisesRegex(capture.CaptureReadError, "1 of 2"):
                    next(frames)

    def test_cli_reports_change_and_fails_on_short_or_static_clip(self):
        for pixels, status in (([1, 2], 0), ([1, 1], 1), ([1], 1)):
            with self.subTest(pixels=pixels):
                video = FakeVideo([(value, i * 40) for i, value in enumerate(pixels)])
                out = StringIO()
                with patch.object(cv2, "VideoCapture", return_value=video), redirect_stdout(out):
                    self.assertEqual(main(["--file", str(self.clip), "--frames", "2"]), status)
                self.assertIn("backend=FAKE", out.getvalue())
                self.assertIn(f"frames={len(pixels)}/2", out.getvalue())


class CameraTests(TestCase):
    def test_latest_frame_replaces_unread_frames_and_disconnect_is_reported(self):
        first_delivered = Event()
        latest_published = Event()
        disconnect = Event()
        self.addCleanup(disconnect.set)
        self.addCleanup(first_delivered.set)

        def images():
            yield 1, 0
            first_delivered.wait(2)
            yield 2, 0
            yield 3, 0
            latest_published.set()
            disconnect.wait(2)

        video = FakeVideo(images())
        with patch.object(cv2, "VideoCapture", return_value=video) as factory:
            with capture.open_camera(2) as frames:
                first = next(frames)
                first_delivered.set()
                self.assertTrue(latest_published.wait(2))
                latest = next(frames)
                disconnect.set()
                with self.assertRaises(capture.LiveFeedLost):
                    next(frames)
        factory.assert_called_once_with(2, cv2.CAP_AVFOUNDATION)
        self.assertEqual(int(first.frame_bgr[0, 0, 0]), 1)
        self.assertEqual(int(latest.frame_bgr[0, 0, 0]), 3)
        self.assertGreater(latest.timestamp_ms, first.timestamp_ms)
        self.assertTrue(video.released.is_set())

    def test_blocked_read_times_out_before_or_after_first_frame(self):
        for started in (False, True):
            with self.subTest(started=started):
                unblock = Event()

                def images():
                    if started:
                        yield 1, 0
                    unblock.wait(2)
                    yield 2, 0

                video = FakeVideo(images())
                with patch.object(cv2, "VideoCapture", return_value=video):
                    try:
                        with capture.open_camera(1, stall_timeout_seconds=0.1) as frames:
                            if started:
                                next(frames)
                            failure = capture.LiveFeedLost if started else capture.CaptureOpenError
                            with self.assertRaisesRegex(failure, "timed out"):
                                next(frames)
                    finally:
                        unblock.set()
                    self.assertTrue(video.released.wait(2))

    def test_device_open_failure(self):
        video = FakeVideo(opened=False)
        with patch.object(cv2, "VideoCapture", return_value=video):
            with capture.open_camera(7) as frames:
                with self.assertRaisesRegex(capture.CaptureOpenError, "camera 7"):
                    next(frames)
        self.assertTrue(video.released.is_set())

    def test_startup_deadline_uses_monotonic_capture_clock(self):
        with patch.object(capture, "monotonic", side_effect=[100.0, 105.0]):
            camera = capture._LiveCamera(0, 5.0)
            with self.assertRaisesRegex(capture.CaptureOpenError, "timed out"):
                next(camera)
