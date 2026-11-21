"""Run one camera's person-down pipeline beside the staff app and report feed state."""

from datetime import datetime, timezone
from threading import Lock, Thread
import sys
import time

from . import capture
from .capture import CaptureError
from .pipeline import DeliveryError, PersonDownPipeline


LABELS = {
    "rehearsal": "Rehearsal mode",
    "live": "Live camera",
    "replay": "Rehearsal clip",
    "offline": "Camera offline",
    "finished": "Rehearsal clip ended",
    "detector_unavailable": "Detector unavailable",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FeedState:
    """Thread-safe camera status shared between the worker and HTTP snapshots."""

    def __init__(self, status="rehearsal", detail="Sample checks are ready · camera detector not connected"):
        self._lock = Lock()
        self._state = {"status": status, "label": LABELS[status], "detail": detail,
                       "source": None, "fps": 0.0, "last_frame_at": None}

    def update(self, **fields):
        with self._lock:
            self._state.update(fields)

    def snapshot(self):
        with self._lock:
            return dict(self._state)


class _DeliveryAbandoned(Exception):
    pass


class DetectorWorker:
    """Own the single detector session for one camera, including capture fallback."""

    def __init__(self, *, venue, camera_id, deliver, feed, load_predictor,
                 camera_index=None, replay_path=None, fallback_clip=None,
                 open_camera=capture.open_camera, open_replay=capture.open_replay,
                 delivery_attempts=5, stall_timeout_seconds=5.0,
                 sleep=time.sleep, clock=time.monotonic):
        if (camera_index is None) == (replay_path is None):
            raise ValueError("specify exactly one of camera_index or replay_path")
        self._venue = venue
        self._camera = venue.cameras[camera_id]
        self._zone = venue.zone_for_camera(camera_id)
        self._deliver = deliver
        self._feed = feed
        self._load_predictor = load_predictor
        self._camera_index = camera_index
        self._replay_path = replay_path
        self._fallback_clip = fallback_clip
        self._open_camera = open_camera
        self._open_replay = open_replay
        self._delivery_attempts = delivery_attempts
        self._stall_timeout_seconds = stall_timeout_seconds
        self._sleep = sleep
        self._clock = clock

    def start(self):
        thread = Thread(target=self.run, name="detector-worker", daemon=True)
        thread.start()
        return thread

    def run(self):
        try:
            self._run()
        except _DeliveryAbandoned:
            self._set("offline", detail="Could not save check")
        except Exception as error:  # the thread must never die silently
            print(f"Detector worker stopped: {error}", file=sys.stderr)
            self._set("detector_unavailable", detail=str(error))

    def _run(self):
        try:
            predictor = self._load_predictor()
        except Exception as error:
            self._set("detector_unavailable", detail=str(error))
            return
        pipeline = self._new_pipeline(predictor)
        path = self._replay_path
        if self._camera_index is not None:
            try:
                self._consume(pipeline, self._open_camera(
                    self._camera_index, stall_timeout_seconds=self._stall_timeout_seconds), "live")
                self._set("offline", detail="Camera stream ended")
                return
            except CaptureError as error:
                if pipeline.event_emitted or self._fallback_clip is None:
                    self._set("offline", detail=str(error))
                    return
                print(f"Live camera offline before a check, switching to {self._fallback_clip}: {error}",
                      file=sys.stderr)
                pipeline = self._new_pipeline(predictor)
                path = self._fallback_clip
        try:
            self._consume(pipeline, self._open_replay(path), "replay")
        except CaptureError as error:
            self._set("offline", detail=str(error))
            return
        self._set("finished", detail=f"{path}")

    def _new_pipeline(self, predictor):
        return PersonDownPipeline(predictor, camera_id=self._camera.camera_id,
                                  zone_id=self._camera.zone_id, floor_box=self._camera.floor_box,
                                  **self._camera.detector)

    def _consume(self, pipeline, source_context, source):
        with source_context as frames:
            self._set(source, detail=f"{self._zone.name} · {self._zone.landmark}")
            counted = self._count_frames(frames)
            while True:
                try:
                    pipeline.run(counted, source=source, deliver=self._deliver_enriched)
                    return
                except DeliveryError as error:
                    print(error, file=sys.stderr)
                    self._retry_pending(pipeline)

    def _retry_pending(self, pipeline):
        for _attempt in range(self._delivery_attempts):
            self._sleep(0.2)
            try:
                pipeline.deliver_pending(self._deliver_enriched)
                return
            except DeliveryError as error:
                print(error, file=sys.stderr)
        raise _DeliveryAbandoned()

    def _deliver_enriched(self, event):
        event["zone_name"] = self._zone.name
        event["landmark"] = self._zone.landmark
        return self._deliver(event)

    def _count_frames(self, frames):
        window_started = self._clock()
        count = 0
        for sample in frames:
            count += 1
            now = self._clock()
            if now - window_started >= 1.0:
                self._feed.update(fps=round(count / (now - window_started), 1),
                                  last_frame_at=_utc_now())
                window_started, count = now, 0
            yield sample

    def _set(self, status, **fields):
        self._feed.update(status=status, label=LABELS[status], **fields)
