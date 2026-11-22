"""Frame capture for the detector: one code path for a webcam index or a video file.

    with FrameSource("clips/lying_down_twice.mp4") as src:   # or FrameSource(0) for a camera
        first = src.wait_ready()      # first frame, for frame size / warmup / calibration
        src.start()                   # capture (and file pacing) begins now
        while (frame := src.latest()) is not None:
            ...                       # frame.image (BGR), frame.ts_ms, frame.seq

- A background thread keeps only the newest frame; the consumer always gets the latest
  one and stale frames are dropped (counted in .dropped). Files are paced at their own
  media time so dropping behaves like a live camera. realtime=False delivers every file
  frame instead (deterministic evaluation).
- Timestamps: elapsed capture time for cameras; media time relative to the first frame
  for files (handles variable frame rate), or frame_index / constant_fps if given.
- Failures are explicit: CaptureOpenError (could not open / no first frame in time),
  LiveFeedLost (camera stopped or stalled after delivering frames), CaptureReadError
  (file decode failed, stopped early, or has unusable timestamps). End of file is
  latest() returning None.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Thread
from time import monotonic

import cv2
import numpy as np

DEFAULT_STALL_TIMEOUT_SECONDS = 5.0


class CaptureError(Exception):
    """The capture source cannot continue."""


class CaptureOpenError(CaptureError):
    """The source could not be opened or produced no first frame in time."""


class CaptureReadError(CaptureError):
    """A video file could not supply a usable frame or timestamp."""


class LiveFeedLost(CaptureError):
    """A camera that had supplied frames stopped delivering them."""


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR uint8
    ts_ms: float
    seq: int


def is_bgr(image) -> bool:
    return (
        image is not None
        and getattr(image, "ndim", 0) == 3
        and image.shape[2] == 3
        and image.size > 0
        and image.dtype == np.uint8
    )


def _open_capture(source: int | str):
    if isinstance(source, int):
        if sys.platform == "darwin":
            return cv2.VideoCapture(source, cv2.CAP_AVFOUNDATION)
        return cv2.VideoCapture(source)
    return cv2.VideoCapture(source)


class _FileTimeline:
    """Validated, strictly increasing media timestamps for a video file."""

    def __init__(self, cap, constant_fps: float | None):
        self.cap = cap
        self.constant_fps = constant_fps
        self.reported_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self.index = 0
        self.first_ms: float | None = None
        self.last_ms: float | None = None

    def read(self) -> Frame | None:
        """Next frame, None at a clean end of file; raises CaptureReadError otherwise."""
        ok, image = self.cap.read()
        if not ok:
            if self.index == 0:
                raise CaptureReadError("the clip contains no readable frames")
            rf = self.reported_frames
            if rf and math.isfinite(rf) and self.index < rf:
                raise CaptureReadError(f"decoder stopped after {self.index} of {rf:.0f} reported frames")
            return None
        if not is_bgr(image):
            raise CaptureReadError(f"invalid BGR image at frame {self.index + 1}")
        if self.constant_fps is None:
            media_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            if not math.isfinite(media_ms) or media_ms < 0:
                raise self._timestamp_error()
            if self.first_ms is None:
                self.first_ms = media_ms
            ts_ms = media_ms - self.first_ms
        else:
            ts_ms = self.index * 1000.0 / self.constant_fps
        if not math.isfinite(ts_ms) or ts_ms < 0 or (self.last_ms is not None and ts_ms <= self.last_ms):
            raise self._timestamp_error()
        self.last_ms = ts_ms
        frame = Frame(image, ts_ms, self.index)
        self.index += 1
        return frame

    def _timestamp_error(self) -> CaptureReadError:
        return CaptureReadError(
            f"unusable media timestamp at frame {self.index + 1}; "
            "re-export the clip or pass its known constant frame rate (--constant-fps)"
        )


class FrameSource:
    def __init__(
        self,
        source: int | str | Path,
        *,
        realtime: bool = True,
        constant_fps: float | None = None,
        stall_timeout_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS,
    ):
        if isinstance(source, bool) or (isinstance(source, int) and source < 0):
            raise ValueError("camera index must be a nonnegative integer")
        if constant_fps is not None and (not math.isfinite(constant_fps) or constant_fps <= 0):
            raise ValueError("constant_fps must be finite and positive")
        if not math.isfinite(stall_timeout_seconds) or stall_timeout_seconds <= 0:
            raise ValueError("stall_timeout_seconds must be finite and positive")
        self.is_file = not isinstance(source, int)
        if self.is_file:
            source = str(source)
            if not Path(source).is_file():
                raise CaptureOpenError(f"clip is not a local file: {source}")
        elif constant_fps is not None:
            raise ValueError("constant_fps only applies to video files")
        self.source = source
        self.realtime = realtime or not self.is_file
        self.constant_fps = constant_fps
        self.stall_timeout_seconds = stall_timeout_seconds

        self.backend: str | None = None
        self.source_fps = 30.0
        self.first_frame: np.ndarray | None = None
        self.width = self.height = 0
        self.dropped = 0

        self._cond = Condition()
        self._started = False
        self._closed = False
        self._eof = False
        self._failure: CaptureError | None = None
        self._pending: Frame | None = None
        self._ever_framed = False
        self._activity = monotonic()  # last frame (or open / start) time, for stall detection
        self._thread = Thread(target=self._run, name="frame-capture", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ consumer API

    def __enter__(self) -> "FrameSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def wait_ready(self) -> np.ndarray:
        """Block until the first frame is decoded (sets width/height); raises on failure."""
        with self._cond:
            while self.first_frame is None:
                self._raise_if_failed_or_stalled()
                if self._closed:
                    raise CaptureOpenError("source closed before the first frame")
                self._cond.wait(self._wait_seconds())
            return self.first_frame

    def start(self) -> "FrameSource":
        """Begin delivering frames. Call after model warmup so nothing is dropped at startup."""
        self.wait_ready()
        with self._cond:
            self._started = True
            self._activity = monotonic()
            self._cond.notify_all()
        return self

    def latest(self) -> Frame | None:
        """Newest unseen frame (blocks for one); None at end of file. Raises CaptureError."""
        with self._cond:
            while True:
                # A frame read before a failure is still delivered; the error comes next.
                if self._pending is not None:
                    frame, self._pending = self._pending, None
                    self._cond.notify_all()
                    return frame
                if self._failure is not None:
                    raise self._failure
                if self._eof or self._closed:
                    return None
                self._raise_if_failed_or_stalled()
                self._cond.wait(self._wait_seconds())

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._pending = None
            self._cond.notify_all()
        self._thread.join(timeout=0.5)

    # ------------------------------------------------------------ internals

    def _wait_seconds(self) -> float:
        if self.is_file:
            return 0.5
        return max(0.0, self._activity + self.stall_timeout_seconds - monotonic()) + 0.001

    def _raise_if_failed_or_stalled(self) -> None:
        # Called with the lock held.
        if self._failure is None and not self.is_file:
            if monotonic() >= self._activity + self.stall_timeout_seconds:
                self._fail_locked("timed out waiting for a usable frame")
        if self._failure is not None:
            raise self._failure

    def _fail_locked(self, reason: str, error_type: type[CaptureError] | None = None) -> None:
        if self._closed or self._failure is not None:
            return
        if error_type is None:
            error_type = LiveFeedLost if self._ever_framed else CaptureOpenError
        label = f"camera {self.source}" if not self.is_file else f"clip {self.source}"
        self._failure = error_type(f"{label}: {reason}")
        self._cond.notify_all()

    def _run(self) -> None:
        cap = None
        try:
            cap = _open_capture(self.source)
            if not cap.isOpened():
                with self._cond:
                    what = "AVFoundation device" if not self.is_file else "clip"
                    self._fail_locked(f"OpenCV could not open the {what}", CaptureOpenError)
                return
            self.backend = cap.getBackendName()
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps and math.isfinite(fps) and fps > 1:
                self.source_fps = fps
            if self.is_file:
                self._run_file(cap)
            else:
                self._run_camera(cap)
        except CaptureError as err:
            with self._cond:
                self._fail_locked(str(err), type(err))
        except (cv2.error, OSError, RuntimeError) as err:
            with self._cond:
                self._fail_locked(str(err), CaptureReadError if self.is_file else None)
        finally:
            if cap is not None:
                cap.release()

    def _publish_first(self, image: np.ndarray) -> bool:
        """Expose the first frame, then hold until start(). False if closed meanwhile."""
        with self._cond:
            self.first_frame = image
            self.height, self.width = image.shape[:2]
            self._ever_framed = True
            self._activity = monotonic()
            self._cond.notify_all()
            self._cond.wait_for(lambda: self._started or self._closed)
            return not self._closed

    def _deliver(self, frame: Frame) -> bool:
        """Hand a frame to the consumer. False if the source is closed/failed."""
        with self._cond:
            if self._closed or self._failure is not None:
                return False
            if not self.realtime:
                self._cond.wait_for(lambda: self._pending is None or self._closed)
                if self._closed:
                    return False
            elif self._pending is not None:
                self.dropped += 1
            self._pending = frame
            self._activity = monotonic()
            self._cond.notify_all()
            return True

    def _run_file(self, cap) -> None:
        timeline = _FileTimeline(cap, self.constant_fps)
        first = timeline.read()
        if first is None:  # unreachable: read() raises on an empty clip
            raise CaptureReadError("the clip contains no readable frames")
        if not self._publish_first(first.image):
            return
        t0 = monotonic()
        frame: Frame | None = first
        while frame is not None:
            if self.realtime:
                delay = t0 + frame.ts_ms / 1000.0 - monotonic()
                if delay > 0:
                    with self._cond:
                        if self._cond.wait_for(lambda: self._closed, timeout=delay):
                            return
            if not self._deliver(frame):
                return
            frame = timeline.read()
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def _run_camera(self, cap) -> None:
        ok, image = cap.read()
        if not ok or not is_bgr(image):
            with self._cond:
                self._fail_locked("the camera returned no usable first frame", CaptureOpenError)
            return
        if not self._publish_first(image):
            return
        # The first frame is stale after warmup; live delivery starts from the next read.
        t0 = monotonic()
        last_t = None
        seq = 0
        while True:
            with self._cond:
                if self._closed or self._failure is not None:
                    return
            ok, image = cap.read()
            now = monotonic()
            if not ok or not is_bgr(image):
                with self._cond:
                    self._fail_locked("the camera stopped returning usable frames")
                return
            if last_t is not None and now <= last_t:
                continue
            last_t = now
            if not self._deliver(Frame(image, (now - t0) * 1000.0, seq)):
                return
            seq += 1
