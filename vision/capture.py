"""Deliver timestamped BGR frames from local video and macOS cameras."""

from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
from threading import Condition, Thread
from time import monotonic

import cv2
import numpy as np


class CaptureError(Exception):
    """The capture source cannot continue."""


class CaptureOpenError(CaptureError):
    """The source could not be opened or the live camera failed to start."""


class CaptureReadError(CaptureError):
    """Replay could not supply a usable frame or timestamp."""


class LiveFeedLost(CaptureError):
    """A camera that had supplied frames stopped delivering them."""


@dataclass(frozen=True)
class CapturedFrame:
    frame_bgr: np.ndarray
    timestamp_ms: float


def _is_bgr(frame):
    return (
        frame is not None
        and frame.ndim == 3
        and frame.shape[2] == 3
        and frame.size > 0
        and frame.dtype == np.uint8
    )


class _Replay:
    def __init__(self, capture, constant_fps):
        self._capture = capture
        self._constant_fps = constant_fps
        self._frame_index = 0
        self._first_timestamp_ms = None
        self._last_timestamp_ms = None
        self._finished = False
        self.backend = capture.getBackendName()
        self._reported_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)

    def __iter__(self):
        return self

    def __next__(self):
        if self._finished:
            raise StopIteration
        try:
            return self._read()
        except (CaptureError, StopIteration):
            self._finished = True
            raise
        except cv2.error as error:
            self._finished = True
            raise CaptureReadError(f"OpenCV replay failed: {error}") from error

    def _read(self):
        ok, frame = self._capture.read()
        if not ok:
            if not self._frame_index:
                raise CaptureReadError("the clip contains no readable frames")
            if math.isfinite(self._reported_frames) and self._frame_index < self._reported_frames:
                raise CaptureReadError(
                    f"decoder stopped after {self._frame_index} of "
                    f"{self._reported_frames:.0f} reported frames"
                )
            raise StopIteration
        if not _is_bgr(frame):
            raise CaptureReadError(f"invalid BGR image at frame {self._frame_index + 1}")

        if self._constant_fps is None:
            media_ms = self._capture.get(cv2.CAP_PROP_POS_MSEC)
            if not math.isfinite(media_ms) or media_ms < 0:
                raise self._timestamp_error()
            if self._first_timestamp_ms is None:
                self._first_timestamp_ms = media_ms
            timestamp_ms = media_ms - self._first_timestamp_ms
        else:
            timestamp_ms = self._frame_index * 1000.0 / self._constant_fps
        if (
            not math.isfinite(timestamp_ms)
            or timestamp_ms < 0
            or (
                self._last_timestamp_ms is not None
                and timestamp_ms <= self._last_timestamp_ms
            )
        ):
            raise self._timestamp_error()
        self._last_timestamp_ms = timestamp_ms
        self._frame_index += 1
        return CapturedFrame(frame, timestamp_ms)

    def _timestamp_error(self):
        return CaptureReadError(
            f"unusable media timestamp at frame {self._frame_index + 1}; "
            "re-export the clip or specify its known constant frame rate"
        )

    def close(self):
        self._finished = True
        self._capture.release()


@contextmanager
def open_replay(path, *, constant_fps=None):
    """Read a local clip in decode order, with timestamps relative to its first frame."""
    if constant_fps is not None and (not math.isfinite(constant_fps) or constant_fps <= 0):
        raise ValueError("constant_fps must be finite and positive")
    path = Path(path)
    if not path.is_file():
        raise CaptureOpenError(f"clip is not a local file: {path}")
    capture = replay = None
    try:
        try:
            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                raise CaptureOpenError(f"could not open clip: {path}")
            replay = _Replay(capture, constant_fps)
        except cv2.error as error:
            raise CaptureOpenError(f"could not open clip {path}: {error}") from error
        yield replay
    finally:
        if replay is not None:
            replay.close()
        elif capture is not None:
            capture.release()


class _LiveCamera:
    def __init__(self, device_index, stall_timeout_seconds):
        self._device_index = device_index
        self._timeout_seconds = stall_timeout_seconds
        self._condition = Condition()
        self._started_seconds = monotonic()
        self._last_frame_seconds = None
        self._pending = None
        self._failure = None
        self._closed = False
        self.backend = "AVFOUNDATION"
        self._reader = Thread(target=self._read, name="camera-capture", daemon=True)

    def start(self):
        self._reader.start()

    def __iter__(self):
        return self

    def __next__(self):
        with self._condition:
            while not self._closed:
                deadline = self._deadline_seconds()
                if self._failure is None and monotonic() >= deadline:
                    self._fail_locked("timed out waiting for a usable frame")
                if self._failure is not None:
                    raise self._failure
                if self._pending is not None:
                    frame, self._pending = self._pending, None
                    return frame
                self._condition.wait(max(0.0, deadline - monotonic()))
        raise StopIteration

    def _deadline_seconds(self):
        last_activity = self._last_frame_seconds
        if last_activity is None:
            last_activity = self._started_seconds
        return last_activity + self._timeout_seconds

    def _fail_locked(self, reason):
        if self._closed or self._failure is not None:
            return
        error_type = CaptureOpenError if self._last_frame_seconds is None else LiveFeedLost
        self._failure = error_type(f"camera {self._device_index}: {reason}")
        self._pending = None
        self._condition.notify_all()

    def _read(self):
        capture = None
        try:
            capture = cv2.VideoCapture(self._device_index, cv2.CAP_AVFOUNDATION)
            if not capture.isOpened():
                with self._condition:
                    self._fail_locked("OpenCV could not open the AVFoundation device")
                return
            while True:
                with self._condition:
                    if self._closed or self._failure is not None:
                        return
                ok, frame = capture.read()
                with self._condition:
                    now = monotonic()
                    if self._closed or self._failure is not None:
                        return
                    if now >= self._deadline_seconds():
                        self._fail_locked("timed out waiting for a usable frame")
                        return
                    if not ok or not _is_bgr(frame):
                        self._fail_locked("the camera stopped returning usable frames")
                        return
                    if self._last_frame_seconds is not None and now <= self._last_frame_seconds:
                        continue
                    self._last_frame_seconds = now
                    self._pending = CapturedFrame(frame, (now - self._started_seconds) * 1000.0)
                    self._condition.notify_all()
        except (cv2.error, OSError, RuntimeError) as error:
            with self._condition:
                self._fail_locked(str(error))
        finally:
            if capture is not None:
                capture.release()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._reader.join(timeout=0.25)


@contextmanager
def open_camera(device_index, *, stall_timeout_seconds=5.0):
    """Read the latest frame from a macOS camera with a bounded wait for input."""
    if not isinstance(device_index, int) or isinstance(device_index, bool) or device_index < 0:
        raise ValueError("device_index must be a nonnegative integer")
    if not math.isfinite(stall_timeout_seconds) or stall_timeout_seconds <= 0:
        raise ValueError("stall_timeout_seconds must be finite and positive")
    camera = _LiveCamera(device_index, stall_timeout_seconds)
    camera.start()
    try:
        yield camera
    finally:
        camera.close()
