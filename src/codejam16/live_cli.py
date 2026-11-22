"""Run person-down detection and PoseC3D on a camera or paced recording."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import sys
from threading import Event, Lock, Thread
import time

from .detector import PersonDownDetector
from .illness import IllnessEpisode
from .store import IncidentStore
from .pose_trace import _trace_pose
from .posec3d_cli import load_scorer, windows
from .vision_cli import ReplayError, _load_pose_predictor


def put_latest(queue, value):
    """Single producer, single consumer, one pending item."""
    try:
        queue.put_nowait(value)
    except Full:
        try:
            queue.get_nowait()
        except Empty:
            pass
        queue.put_nowait(value)


def capture_frames(source, output, stopped):
    import cv2

    cap = None
    try:
        cap = cv2.VideoCapture(int(source) if source.isdecimal() else source)
        if not cap.isOpened():
            raise RuntimeError(f"could not open camera or stream {source}")
        recording = Path(source).is_file()
        start = time.monotonic()
        while not stopped.is_set():
            ok, frame = cap.read()
            if not ok:
                if recording:
                    break
                raise RuntimeError("camera or stream disconnected")
            if recording:
                timestamp = cap.get(cv2.CAP_PROP_POS_MSEC)
                if stopped.wait(max(0, start + timestamp / 1000 - time.monotonic())):
                    return
            else:
                timestamp = (time.monotonic() - start) * 1000
            put_latest(output, (timestamp, frame))
        terminal = None
    except Exception as error:
        terminal = error
    finally:
        if cap is not None:
            cap.release()
    while not stopped.is_set():
        try:
            output.put(terminal, timeout=0.2)
            return
        except Full:
            pass


def pose_rows(args, frames, predictor, emit, scoring):
    observation = {}

    def observe(timestamp, shape, results):
        joints, status = _trace_pose(results, *shape)
        observation.update(source_timestamp_ms=timestamp, image_shape=list(shape),
                           joints=joints, status=status)

    detector = PersonDownDetector(predictor, floor_box=args.floor_box,
                                  source="replay" if Path(args.source).is_file() else "live",
                                  camera_id=args.camera_id, zone_id=args.zone, observe_pose=observe)
    interval_ms = 1000 / args.pose_fps
    next_ms = None
    while True:
        if scoring.done():
            scoring.result()
        try:
            item = frames.get(timeout=0.25)
        except Empty:
            continue
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        timestamp, frame = item
        if next_ms is not None and timestamp + 0.001 < next_ms:
            continue
        if next_ms is None:
            next_ms = timestamp
        next_ms += (1 + max(0, math.floor((timestamp - next_ms) / interval_ms))) * interval_ms
        with redirect_stdout(sys.stderr):
            event = detector.update(frame, timestamp)
        if event:
            emit(event)
        yield observation.copy()


def worker():
    root = Path("local/mmaction2-1.2.0")
    scorer = load_scorer(argparse.Namespace(
        labels=root / "tools/data/skeleton/label_map_ntu60.txt",
        config=root / "configs/skeleton/posec3d/slowonly_r50_8xb16-u48-240e_ntu60-xsub-keypoint.py",
        checkpoint=Path("local/posec3d-ntu60.pth"), cpu_threads=4))
    print("ready", flush=True)
    for line in sys.stdin:
        print(json.dumps(scorer(*json.loads(line)), allow_nan=False), flush=True)


def score_windows(process, jobs, emit):
    while (window := jobs.get()) is not None:
        process.stdin.write(json.dumps(window) + "\n")
        process.stdin.flush()
        response = process.stdout.readline()
        if not response:
            raise RuntimeError("PoseC3D worker exited during scoring")
        emit(json.loads(response))


def run(args):
    import numpy as np

    with redirect_stdout(sys.stderr):
        predictor = _load_pose_predictor(Path("local/yolo26n-pose.pt"))
        predictor(np.zeros((640, 640, 3), dtype=np.uint8))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        ["src", "local/mmaction2-1.2.0", os.environ.get("PYTHONPATH", "")]))
    process = subprocess.Popen(
        ["local/posec3d-env/bin/python", "-m", __spec__.name, "--worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    stopped = Event()
    frames = Queue(maxsize=1)
    reader = Thread(target=capture_frames, args=(args.source, frames, stopped), daemon=True)
    pool = ThreadPoolExecutor(max_workers=1)
    jobs = Queue(maxsize=1)
    episode = IllnessEpisode()
    store = None
    if args.staff_database:
        args.staff_database.parent.mkdir(parents=True, exist_ok=True)
        store = IncidentStore(str(args.staff_database))
    source_kind = "replay" if Path(args.source).is_file() else "live"
    zones = {"east-bar": ("East bar", "Beside the side stairs"),
             "main-floor": ("Main floor", "Near the north tables")}
    output = sys.stdout
    output_lock = Lock()

    def emit(record):
        with output_lock:
            print(json.dumps(record), file=output, flush=True)
            if "nausea_vomiting_score" not in record and record.get("status") != "insufficient_evidence":
                return
            alert = episode.update(record)
            if alert:
                zone_name, landmark = zones[args.zone]
                alert.update(source=source_kind, camera_id=args.camera_id, zone_id=args.zone,
                             zone_name=zone_name, landmark=landmark)
                if store is not None:
                    store.create_incident(alert)
                print(json.dumps(alert), file=output, flush=True)

    try:
        if process.stdout.readline().strip() != "ready":
            raise RuntimeError("PoseC3D worker failed to start")
        scoring = pool.submit(score_windows, process, jobs, emit)
        print(f"Models ready. Sampling at up to {args.pose_fps:g} FPS.", file=sys.stderr)
        reader.start()
        rows = pose_rows(args, frames, predictor, emit, scoring)
        for window in windows(rows, 4000, 2000, 500):
            put_latest(jobs, window)
        while not scoring.done():
            try:
                jobs.put(None, timeout=0.25)
                break
            except Full:
                pass
        scoring.result()
    finally:
        stopped.set()
        put_latest(jobs, None)
        process.terminate()
        process.wait()
        pool.shutdown()
        if reader.is_alive():
            reader.join(timeout=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="0", help="camera index, stream URL or recorded clip")
    parser.add_argument("--pose-fps", type=float, default=12)
    parser.add_argument("--floor-box", nargs=4, type=float, required=True)
    parser.add_argument("--staff-database", type=Path, help="opt in to publishing illness checks to this staff SQLite file")
    parser.add_argument("--camera-id", default="demo-camera")
    parser.add_argument("--zone", choices=("east-bar", "main-floor"), default="east-bar")
    args = parser.parse_args()
    if not math.isfinite(args.pose_fps) or args.pose_fps <= 0:
        parser.error("--pose-fps must be finite and positive")
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, ReplayError) as error:
        parser.exit(1, f"Live detector failed: {error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker()
    else:
        sys.exit(main())
