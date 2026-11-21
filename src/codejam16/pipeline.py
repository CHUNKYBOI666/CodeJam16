"""Process a capture source and retain events until a caller accepts them."""

from collections import Counter
from contextlib import redirect_stdout
import sys
import time

from .capture import CaptureError, CaptureOpenError, LiveFeedLost, open_camera, open_replay
from .detector import PersonDownDetector


class DeliveryError(Exception):
    """An event is pending because the consumer could not accept it."""


class PersonDownPipeline:
    """Own a single source session and one pending incident signal."""

    def __init__(self, predict_pose, *, camera_id, zone_id, floor_box,
                 confirmation_seconds=2.0, rearm_seconds=2.0,
                 maximum_observation_gap_ms=500, minimum_keypoint_confidence=0.4,
                 down_angle_degrees=30, upright_angle_degrees=60):
        self.predict_pose = predict_pose
        self.detector_options = dict(
            camera_id=camera_id, zone_id=zone_id, floor_box=floor_box,
            confirmation_seconds=confirmation_seconds, rearm_seconds=rearm_seconds,
            maximum_observation_gap_ms=maximum_observation_gap_ms,
            minimum_keypoint_confidence=minimum_keypoint_confidence,
            down_angle_degrees=down_angle_degrees,
            upright_angle_degrees=upright_angle_degrees,
        )
        self.detector = None
        self.pending_event = None
        self.event_emitted = False
        self.feed_state = "idle"
        self.source = None

    def deliver_pending(self, deliver):
        if self.pending_event is None:
            return
        try:
            deliver(self.pending_event.copy())
        except Exception as error:
            raise DeliveryError(
                f"delivery failed for episode {self.pending_event['episode_id']}: {error}"
            ) from error
        self.pending_event = None

    def run(self, frames, *, source, deliver):
        """Consume ordered frames. A successful return from deliver acknowledges the event."""
        if source not in ("live", "replay"):
            raise ValueError("source must be live or replay")
        if self.pending_event is not None:
            self.deliver_pending(deliver)
        if self.detector is None:
            self.detector = PersonDownDetector(self.predict_pose, source=source, **self.detector_options)
            self.source = source
        elif source != self.source:
            if self.event_emitted:
                raise ValueError("cannot change source while a confirmed episode is active")
            self.detector = PersonDownDetector(self.predict_pose, source=source, **self.detector_options)
            self.source = source

        count = events = gaps = 0
        unknown = Counter()
        previous_ms = None
        start = time.perf_counter()
        self.feed_state = "online"
        try:
            for sample in frames:
                if self.pending_event is not None:
                    self.deliver_pending(deliver)
                timestamp_ms = sample.timestamp_ms
                if previous_ms is not None and timestamp_ms - previous_ms > self.detector_options["maximum_observation_gap_ms"]:
                    gaps += 1
                with redirect_stdout(sys.stderr):
                    event = self.detector.update(sample.frame_bgr, timestamp_ms)
                count += 1
                previous_ms = timestamp_ms
                if self.detector.last_unknown_reason:
                    unknown[self.detector.last_unknown_reason] += 1
                if event is not None:
                    self.event_emitted = True
                    self.pending_event = event
                    self.deliver_pending(deliver)
                    events += 1
                    print(f"Confirmed {event['episode_id']} at {timestamp_ms:.1f} ms", file=sys.stderr)
        except CaptureError:
            self.feed_state = "offline"
            raise
        except DeliveryError:
            self.feed_state = "delivery_error"
            raise
        except (ValueError, RuntimeError, OSError):
            self.feed_state = "detector_error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            fps = count / elapsed if elapsed else 0.0
            print(f"{source} summary: frames={count} events={events} gaps={gaps} "
                  f"unknown={sum(unknown.values())} elapsed_seconds={elapsed:.3f} "
                  f"fps={fps:.1f} last_source_ms={previous_ms}", file=sys.stderr)
        self.feed_state = "finished" if source == "replay" else "offline"

    def run_camera(self, index, deliver, *, stall_timeout_seconds=5.0):
        try:
            with open_camera(index, stall_timeout_seconds=stall_timeout_seconds) as frames:
                self.run(frames, source="live", deliver=deliver)
        except (CaptureOpenError, LiveFeedLost):
            self.feed_state = "offline"
            raise

    def run_replay(self, path, deliver, *, constant_fps=None):
        try:
            with open_replay(path, constant_fps=constant_fps) as frames:
                self.run(frames, source="replay", deliver=deliver)
        except CaptureError:
            self.feed_state = "offline"
            raise
