"""Replay one local clip and write person-down events as JSON Lines."""

import argparse
from contextlib import ExitStack
from contextlib import redirect_stdout
from functools import partial
import json
from pathlib import Path
import pickle
import sys

from .capture import CaptureError, CaptureOpenError, LiveFeedLost
from .pipeline import DeliveryError, PersonDownPipeline
from .pose_trace import PoseTrace
from .venue import DEFAULT_VENUE_PATH, load_venue


class ReplayError(Exception):
    """The replay could not be completed."""


THRESHOLDS = ("confirmation_seconds", "rearm_seconds", "maximum_observation_gap_ms",
              "minimum_keypoint_confidence", "down_angle_degrees", "upright_angle_degrees")
DEFAULT_THRESHOLDS = dict(confirmation_seconds=2.0, rearm_seconds=2.0, maximum_observation_gap_ms=500,
                          minimum_keypoint_confidence=0.4, down_angle_degrees=30, upright_angle_degrees=60)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", nargs="?", type=Path, help="Path to a local staged recording")
    parser.add_argument("--camera-index", type=int, help="macOS camera index")
    parser.add_argument("--fallback-clip", type=Path, help="Explicit replay fallback on pre-alert camera failure")
    parser.add_argument("--constant-fps", type=float, help="Known constant frame rate for replay")
    parser.add_argument("--stall-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--pose-trace", type=Path, help="Write an optional local pose JSONL file")
    parser.add_argument(
        "--weights",
        type=Path,
        default=Path("local/yolo26n-pose.pt"),
        help="Path to the pretrained YOLO26n-pose checkpoint (default: %(default)s)",
    )
    parser.add_argument("--venue", type=Path, default=DEFAULT_VENUE_PATH,
                        help="Venue config supplying zone, floor box and thresholds (default: %(default)s)")
    parser.add_argument(
        "--floor-box",
        nargs=4,
        type=float,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Override the venue floor rectangle, as fractions of frame width and height",
    )
    parser.add_argument("--camera-id", default="demo-camera")
    parser.add_argument("--zone-id")
    for name in THRESHOLDS:
        parser.add_argument(f"--{name.replace('_', '-')}", type=float)
    return parser.parse_args(argv)


def _detector_options(args):
    """Combine the venue camera's settings with any explicit command-line overrides."""
    camera = load_venue(args.venue).cameras.get(args.camera_id)
    options = dict(DEFAULT_THRESHOLDS)
    if camera is not None:
        options.update(camera.detector, zone_id=camera.zone_id, floor_box=camera.floor_box)
    options["camera_id"] = args.camera_id
    if args.zone_id is not None or "zone_id" not in options:
        options["zone_id"] = args.zone_id or "east-bar"
    if args.floor_box is not None:
        options["floor_box"] = tuple(args.floor_box)
    if "floor_box" not in options:
        raise ValueError(f"camera {args.camera_id} is not in {args.venue}; pass --floor-box")
    options.update({name: getattr(args, name) for name in THRESHOLDS if getattr(args, name) is not None})
    return options


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


def _print_event(event):
    print(json.dumps(event), flush=True)


def main(argv=None):
    args = _parse_args(argv)
    if (args.clip is None) == (args.camera_index is None):
        print("Specify either a clip or --camera-index", file=sys.stderr)
        return 2
    if args.clip is not None and args.fallback_clip is not None:
        print("--fallback-clip requires --camera-index", file=sys.stderr)
        return 2
    try:
        with ExitStack() as resources:
            trace = None
            if args.pose_trace is not None:
                trace = PoseTrace(resources.enter_context(args.pose_trace.open("x", encoding="utf-8")))
            _run(args, trace)
            if trace is not None and trace.failed:
                return 1
        return 0
    except (ReplayError, CaptureError, DeliveryError, ImportError, OSError, ValueError, RuntimeError) as error:
        print(f"Vision pipeline failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Vision pipeline interrupted", file=sys.stderr)
        return 130


def _run(args, observe_pose):
    options = _detector_options(args)
    if not args.weights.is_file():
        raise ReplayError(f"pose checkpoint is not a local file: {args.weights}")
    with redirect_stdout(sys.stderr):
        predictor = _load_pose_predictor(args.weights)
    pipeline = PersonDownPipeline(predictor, observe_pose=observe_pose, **options)
    if args.clip is not None:
        pipeline.run_replay(args.clip, _print_event, constant_fps=args.constant_fps)
        print("End of replay", file=sys.stderr)
        return
    try:
        pipeline.run_camera(args.camera_index, _print_event,
                            stall_timeout_seconds=args.stall_timeout_seconds)
    except (CaptureOpenError, LiveFeedLost) as error:
        print(f"Live camera offline: {error}", file=sys.stderr)
        if args.fallback_clip is None or pipeline.event_emitted:
            raise
        print(f"Switching to labeled replay: {args.fallback_clip}", file=sys.stderr)
        pipeline.run_replay(args.fallback_clip, _print_event, constant_fps=args.constant_fps)
        print("End of replay", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
