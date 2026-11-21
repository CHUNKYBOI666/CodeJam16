"""Replay one local clip and write person-down events as JSON Lines."""

import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from functools import partial
import json
import math
from pathlib import Path
import pickle
import sys
import time

from .detector import PersonDownDetector
from .pose_trace import PoseTrace


class ReplayError(Exception):
    """The replay could not be completed."""


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path, help="Path to a local staged recording")
    parser.add_argument(
        "--weights",
        type=Path,
        default=Path("local/yolo26n-pose.pt"),
        help="Path to the pretrained YOLO26n-pose checkpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--floor-box",
        nargs=4,
        type=float,
        required=True,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Floor rectangle as fractions of frame width and height",
    )
    parser.add_argument("--camera-id", default="demo-camera")
    parser.add_argument("--zone-id", default="demo-area")
    parser.add_argument("--confirmation-seconds", type=float, default=2.0)
    parser.add_argument("--rearm-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-observation-gap-ms", type=float, default=500)
    parser.add_argument("--minimum-keypoint-confidence", type=float, default=0.4)
    parser.add_argument("--down-angle-degrees", type=float, default=30)
    parser.add_argument("--upright-angle-degrees", type=float, default=60)
    parser.add_argument("--pose-trace", type=Path, help="Write an optional local pose JSONL file")
    return parser.parse_args(argv)


def _load_pose_predictor(weights):
    try:
        from ultralytics import YOLO

        model = YOLO(str(weights))
        if model.task != "pose":
            raise ReplayError("the checkpoint does not contain a pose model")
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        pickle.UnpicklingError,
    ) as error:
        raise ReplayError(f"could not load pose checkpoint {weights}: {error}") from error
    return partial(model.predict, device="cpu", verbose=False)


def _process_frames(capture, cv2, detector, maximum_observation_gap_ms):
    reported_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    frames = events = gaps = 0
    unknown_reasons = Counter()
    last_timestamp_ms = None
    started_seconds = time.perf_counter()

    while True:
        read_ok, frame_bgr = capture.read()
        if not read_ok:
            break
        if frame_bgr is None or frame_bgr.size == 0:
            raise ReplayError(f"decoder returned an empty frame after {frames} frames")
        if len(frame_bgr.shape) != 3 or frame_bgr.shape[2] != 3:
            raise ReplayError(f"decoder returned a non-BGR frame at frame {frames + 1}")
        timestamp_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
        if (
            not math.isfinite(timestamp_ms)
            or timestamp_ms < 0
            or (last_timestamp_ms is not None and timestamp_ms <= last_timestamp_ms)
        ):
            raise ReplayError(
                f"unusable media timestamp at frame {frames + 1}: "
                f"{timestamp_ms} ms (previous: {last_timestamp_ms} ms); "
                "use an export with increasing media timestamps"
            )
        if (
            last_timestamp_ms is not None
            and timestamp_ms - last_timestamp_ms > maximum_observation_gap_ms
        ):
            gaps += 1

        try:
            with redirect_stdout(sys.stderr):
                event = detector.update(frame_bgr, timestamp_ms)
        except (OSError, RuntimeError, ValueError, cv2.error) as error:
            raise ReplayError(
                f"pose processing failed at frame {frames + 1} ({timestamp_ms:.1f} ms): {error}"
            ) from error
        if detector.last_unknown_reason is not None:
            unknown_reasons[detector.last_unknown_reason] += 1
        frames += 1
        last_timestamp_ms = timestamp_ms
        if event is not None:
            try:
                print(json.dumps(event), flush=True)
            except OSError as error:
                raise ReplayError(
                    f"could not write event {event['episode_id']}: {error}"
                ) from error
            events += 1
            print(f"Confirmed {event['episode_id']} at {timestamp_ms:.1f} ms", file=sys.stderr)

    if not frames:
        raise ReplayError("no readable frames")

    elapsed_seconds = time.perf_counter() - started_seconds
    fps = frames / elapsed_seconds if elapsed_seconds > 0 else 0.0
    reason_counts = ", ".join(
        f"{reason}={count}" for reason, count in sorted(unknown_reasons.items())
    )
    print(
        f"Replay summary: frames={frames} events={events} gaps={gaps} "
        f"unknown={sum(unknown_reasons.values())} elapsed_seconds={elapsed_seconds:.3f} "
        f"fps={fps:.1f} last_media_ms={last_timestamp_ms:.1f}"
        + (f" ({reason_counts})" if reason_counts else ""),
        file=sys.stderr,
    )
    if math.isfinite(reported_frames) and reported_frames > frames:
        raise ReplayError(
            f"decoder stopped after {frames} of {reported_frames:.0f} reported frames"
        )
    if math.isfinite(reported_frames) and reported_frames == frames:
        print("End of replay: decoded all reported frames", file=sys.stderr)
    else:
        print("End of replay: decoder returned no more frames", file=sys.stderr)


def _run_replay(args, observe_pose=None):
    if not args.clip.is_file():
        raise ReplayError(f"clip is not a local file: {args.clip}")
    if not args.weights.is_file():
        raise ReplayError(f"pose checkpoint is not a local file: {args.weights}")

    with redirect_stdout(sys.stderr):
        import cv2

        predict_pose = _load_pose_predictor(args.weights)
    detector = PersonDownDetector(
        predict_pose,
        floor_box=args.floor_box,
        camera_id=args.camera_id,
        zone_id=args.zone_id,
        confirmation_seconds=args.confirmation_seconds,
        rearm_seconds=args.rearm_seconds,
        maximum_observation_gap_ms=args.maximum_observation_gap_ms,
        minimum_keypoint_confidence=args.minimum_keypoint_confidence,
        down_angle_degrees=args.down_angle_degrees,
        upright_angle_degrees=args.upright_angle_degrees,
        observe_pose=observe_pose,
    )

    try:
        capture = cv2.VideoCapture(str(args.clip))
        try:
            if not capture.isOpened():
                raise ReplayError("OpenCV could not open the clip")
            print(
                f"Replay {args.clip} via {capture.getBackendName()}, weights={args.weights}, "
                f"floor_box={tuple(args.floor_box)}, "
                f"confirmation_seconds={args.confirmation_seconds}, "
                f"rearm_seconds={args.rearm_seconds}, "
                f"maximum_observation_gap_ms={args.maximum_observation_gap_ms}, "
                f"minimum_keypoint_confidence={args.minimum_keypoint_confidence}, "
                f"down_angle_degrees={args.down_angle_degrees}, "
                f"upright_angle_degrees={args.upright_angle_degrees}",
                file=sys.stderr,
            )
            _process_frames(capture, cv2, detector, args.maximum_observation_gap_ms)
        finally:
            capture.release()
    except cv2.error as error:
        raise ReplayError(f"OpenCV replay error: {error}") from error


def main(argv=None):
    args = _parse_args(argv)
    try:
        with ExitStack() as resources:
            trace = None
            if args.pose_trace is not None:
                trace = PoseTrace(resources.enter_context(args.pose_trace.open("x", encoding="utf-8")))
            _run_replay(args, observe_pose=trace)
            if trace is not None and trace.failed:
                return 1
    except (ReplayError, ImportError, OSError, ValueError) as error:
        print(f"Replay failed for {args.clip}: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Replay interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
