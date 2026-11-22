"""Capture tests with a fake cv2.VideoCapture (no camera, no real video).

Ported from the original src/codejam16 capture adapter tests, plus cases for latest-frame
dropping, every-frame delivery and the pre-start first frame.
"""

from __future__ import annotations

import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Event
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import capture
from capture import CaptureOpenError, CaptureReadError, FrameSource, LiveFeedLost
from capture_probe import main as probe_main

CLIP = Path(__file__)  # any existing file; VideoCapture is faked


def image(value):
    return np.full((2, 3, 3), value, dtype=np.uint8)


class FakeVideo:
    def __init__(self, frames=(), *, opened=True, reported_frames=0, fps=30.0):
        self.frames = iter(frames)
        self.opened = opened
        self.reported_frames = reported_frames
        self.fps = fps
        self.timestamp_ms = 0
        self.released = Event()

    def isOpened(self):
        return self.opened

    def getBackendName(self):
        return "FAKE"

    def get(self, prop):
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.timestamp_ms
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.reported_frames
        return 0

    def read(self):
        nxt = next(self.frames, None)
        if nxt is None:
            return False, None
        value, self.timestamp_ms = nxt
        return True, image(value)

    def release(self):
        self.released.set()


def replay(video, **kw):
    kw.setdefault("realtime", False)
    with patch.object(cv2, "VideoCapture", return_value=video):
        src = FrameSource(CLIP, **kw)
        src.wait_ready()
    return src


def drain(src):
    src.start()
    out = []
    while (f := src.latest()) is not None:
        out.append(f)
    return out


# ---------------------------------------------------------------- replay


def test_media_time_relative_to_first_frame_and_eof():
    video = FakeVideo([(2, 80), (5, 126.5), (9, 180)], reported_frames=3)
    with replay(video) as src:
        assert src.backend == "FAKE"
        assert int(src.first_frame[0, 0, 0]) == 2 and (src.width, src.height) == (3, 2)
        frames = drain(src)
        assert src.latest() is None
    assert [f.ts_ms for f in frames] == [0, 46.5, 100]
    assert [int(f.image[0, 0, 0]) for f in frames] == [2, 5, 9]
    assert [f.seq for f in frames] == [0, 1, 2]
    assert video.released.wait(2)


def test_constant_fps_ignores_backend_timestamps():
    video = FakeVideo([(2, 0), (3, 0), (4, 0)])
    with replay(video, constant_fps=25) as src:
        assert [f.ts_ms for f in drain(src)] == [0, 40, 80]


@pytest.mark.parametrize("bad_ms", [0, -1, float("nan")])
def test_repeated_or_invalid_media_time_is_an_error(bad_ms):
    video = FakeVideo([(2, 0), (3, bad_ms)])
    with replay(video) as src:
        with pytest.raises(CaptureReadError, match="timestamp"):
            drain(src)
    assert video.released.wait(2)


def test_failed_open_is_capture_open_error():
    video = FakeVideo(opened=False)
    with pytest.raises(CaptureOpenError):
        replay(video)
    assert video.released.wait(2)


def test_missing_file_is_capture_open_error():
    with pytest.raises(CaptureOpenError):
        FrameSource("does/not/exist.mp4")


def test_empty_clip_is_read_error():
    with pytest.raises(CaptureReadError, match="no readable frames"):
        replay(FakeVideo([]))


def test_early_decoder_stop_is_distinct_from_eof():
    video = FakeVideo([(2, 0)], reported_frames=2)
    with replay(video) as src:
        src.start()
        assert src.latest().seq == 0
        with pytest.raises(CaptureReadError, match="1 of 2"):
            src.latest()


def test_invalid_image_is_read_error():
    video = FakeVideo([(2, 0)])
    video.read = lambda: (True, np.zeros((2, 3), np.uint8))  # grayscale, not BGR
    with pytest.raises(CaptureReadError, match="BGR"):
        replay(video)


def test_every_frame_mode_never_drops():
    video = FakeVideo([(i, i * 10) for i in range(50)])
    with replay(video) as src:
        src.start()
        got = []
        while (f := src.latest()) is not None:
            time.sleep(0.001)  # slow consumer
            got.append(f.seq)
    assert got == list(range(50)) and src.dropped == 0


def test_realtime_replay_drops_stale_frames_for_slow_consumer():
    # 100 frames at 200 fps (0.5 s of media); consumer takes ~20 ms per frame.
    video = FakeVideo([(i % 255, i * 5) for i in range(100)])
    with replay(video, realtime=True) as src:
        src.start()
        got = []
        while (f := src.latest()) is not None:
            time.sleep(0.02)
            got.append(f.seq)
    assert src.dropped > 50
    assert got == sorted(got) and got[0] == 0
    assert len(got) + src.dropped == 100


def test_nothing_delivered_before_start():
    video = FakeVideo([(1, 0), (2, 10)])
    with replay(video) as src:
        time.sleep(0.05)
        assert src._pending is None  # held until start()


def test_constant_fps_rejected_for_camera_and_bad_values():
    with pytest.raises(ValueError):
        FrameSource(0, constant_fps=30)
    with pytest.raises(ValueError):
        FrameSource(CLIP, constant_fps=0)
    with pytest.raises(ValueError):
        FrameSource(-1)


# ---------------------------------------------------------------- probe CLI


@pytest.mark.parametrize("pixels,status", [([1, 2], 0), ([1, 1], 1), ([1], 1)])
def test_probe_reports_change_and_fails_on_short_or_static_clip(pixels, status):
    video = FakeVideo([(v, i * 40) for i, v in enumerate(pixels)])
    out = StringIO()
    with patch.object(cv2, "VideoCapture", return_value=video), redirect_stdout(out):
        assert probe_main(["--source", str(CLIP), "--frames", "2"]) == status
    assert "backend=FAKE" in out.getvalue()
    assert f"frames={len(pixels)}/2" in out.getvalue()


# ---------------------------------------------------------------- camera


def camera(video, index=2, **kw):
    with patch.object(cv2, "VideoCapture", return_value=video) as factory:
        src = FrameSource(index, **kw)
        src.wait_ready()
    return src, factory


def test_camera_latest_frame_replaces_unread_and_disconnect_is_reported():
    second_read = Event()
    latest_published = Event()
    disconnect = Event()

    def images():
        yield 0, 0  # first frame (size / warmup), not delivered after start
        yield 1, 0
        second_read.wait(2)
        yield 2, 0
        yield 3, 0
        latest_published.set()
        disconnect.wait(2)

    video = FakeVideo(images())
    try:
        src, factory = camera(video)
        with src:
            src.start()
            first = src.latest()
            second_read.set()
            assert latest_published.wait(2)
            time.sleep(0.05)
            latest = src.latest()
            disconnect.set()
            with pytest.raises(LiveFeedLost, match="stopped returning"):
                src.latest()
    finally:
        second_read.set()
        disconnect.set()
    if capture.sys.platform == "darwin":
        factory.assert_called_once_with(2, cv2.CAP_AVFOUNDATION)
    assert int(first.image[0, 0, 0]) == 1
    assert int(latest.image[0, 0, 0]) == 3
    assert src.dropped >= 1
    assert latest.ts_ms > first.ts_ms
    assert video.released.wait(2)


@pytest.mark.parametrize("started", [False, True])
def test_blocked_read_times_out_before_or_after_first_frame(started):
    unblock = Event()

    def images():
        if started:
            yield 1, 0
        unblock.wait(2)
        yield 2, 0

    video = FakeVideo(images())
    try:
        with patch.object(cv2, "VideoCapture", return_value=video):
            src = FrameSource(1, stall_timeout_seconds=0.1)
            with src:
                if not started:
                    with pytest.raises(CaptureOpenError, match="timed out"):
                        src.wait_ready()
                else:
                    src.wait_ready()
                    src.start()
                    with pytest.raises(LiveFeedLost, match="timed out"):
                        src.latest()
    finally:
        unblock.set()
    assert video.released.wait(2)


def test_camera_open_failure():
    video = FakeVideo(opened=False)
    with patch.object(cv2, "VideoCapture", return_value=video):
        src = FrameSource(7)
        with src, pytest.raises(CaptureOpenError, match="camera 7"):
            src.wait_ready()
    assert video.released.wait(2)


def test_warmup_time_before_start_does_not_count_as_stall():
    def images():
        yield 1, 0
        while True:
            time.sleep(0.01)
            yield 2, 0

    video = FakeVideo(images())
    src, _ = camera(video, stall_timeout_seconds=0.1)
    with src:
        time.sleep(0.3)  # "model warmup" longer than the stall timeout
        src.start()
        assert src.latest() is not None
