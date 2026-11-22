"""Score local YOLO pose traces using the pretrained NTU60 PoseC3D checkpoint."""

import argparse
from bisect import bisect_left
from collections import deque
from contextlib import redirect_stdout
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import sys
import time


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def validate_row(row):
    """Reject malformed traces before handing them to the model."""
    timestamp = row["source_timestamp_ms"]
    if not _number(timestamp) or timestamp < 0:
        raise ValueError("source timestamp must be finite and nonnegative")
    height, width = row["image_shape"]
    if any(type(value) is not int or value <= 0 for value in (height, width)):
        raise ValueError("image dimensions must be positive integers")
    joints = row["joints"]
    if joints is None:
        return
    if len(joints) != 17:
        raise ValueError("each pose must contain 17 joints")
    for x, y, score in joints:
        if not all(_number(value) for value in (x, y, score)):
            raise ValueError("joint values must be finite numbers")
        if not (0 <= x < width and 0 <= y < height and 0 <= score <= 1):
            raise ValueError("joint coordinates or confidence out of range")


def windows(rows, duration_ms, stride_ms, gap_ms):
    """Stream full windows, resetting on gaps and frame-size changes."""
    if not all(_number(value) and value > 0 for value in (duration_ms, stride_ms, gap_ms)):
        raise ValueError("window duration, stride and gap must be finite and positive")
    pending = deque()
    previous = None
    end_ms = None
    for row in rows:
        validate_row(row)
        timestamp = row["source_timestamp_ms"]
        if previous is not None and timestamp <= previous["source_timestamp_ms"]:
            raise ValueError("source timestamps must be strictly increasing")
        if previous is None or (
            timestamp - previous["source_timestamp_ms"] > gap_ms
            or row["image_shape"] != previous["image_shape"]
        ):
            pending.clear()
            end_ms = timestamp + duration_ms
        pending.append(row)
        previous = row
        while timestamp >= end_ms:
            start_ms = end_ms - duration_ms
            while len(pending) > 1 and pending[1]["source_timestamp_ms"] <= start_ms:
                pending.popleft()
            yield start_ms, end_ms, list(pending)
            end_ms += stride_ms
        # Retain a predecessor for nearest-time sampling without retaining the clip.
        next_start = end_ms - duration_ms
        while len(pending) > 1 and pending[1]["source_timestamp_ms"] <= next_start:
            pending.popleft()


def sample_window(rows, start_ms, end_ms):
    """Select 48 poses spaced evenly in source time, preserving missing data."""
    timestamps = [row["source_timestamp_ms"] for row in rows]
    selected = []
    for index in range(48):
        target = start_ms + (end_ms - start_ms) * index / 47
        right = min(bisect_left(timestamps, target), len(rows) - 1)
        left = max(0, right - 1)
        nearest = min((left, right), key=lambda i: abs(timestamps[i] - target))
        selected.append(rows[nearest]["joints"])
    return selected


def _usable(joints):
    return joints is not None and sum(joint[2] >= 0.4 for joint in joints) >= 8


def load_scorer(args):
    labels = args.labels.read_text(encoding="utf-8").splitlines()
    if len(labels) != 60 or not all(word in labels[47].lower() for word in ("nausea", "vomit")):
        raise ValueError("expected the official NTU60 label map")
    if not args.checkpoint.is_file():
        raise ValueError("checkpoint must be a local file")
    digest = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    if digest != "f3adabf19d56bd4fb458e59570d5bbe0208f1e8a9a79c3d5f7fe03a0d5825d2a":
        raise ValueError("expected the documented NTU60 XSub keypoint checkpoint")
    with redirect_stdout(sys.stderr):
        from mmaction.apis import inference_skeleton, init_recognizer
        from mmengine.config import Config
        import numpy as np
        import torch

        torch.set_num_threads(args.cpu_threads)
        config = Config.fromfile(str(args.config))
        config.test_pipeline = config.val_pipeline
        model = init_recognizer(config, str(args.checkpoint), device="cpu")

    def score_window(start_ms, end_ms, observations):
        selected = sample_window(observations, start_ms, end_ms)
        usable = sum(_usable(joints) for joints in selected)
        result = {"start_ms": start_ms, "end_ms": end_ms, "usable_samples": usable}
        if usable < 36 or any(row.get("status") == "ambiguous" for row in observations):
            result["status"] = "insufficient_evidence"
        else:
            poses = []
            for joints in selected:
                array = np.asarray(joints if joints is not None else [[0, 0, 0]] * 17,
                                   dtype=np.float32)
                poses.append(dict(keypoints=array[None, :, :2],
                                  keypoint_scores=array[None, :, 2]))
            started = time.perf_counter()
            with redirect_stdout(sys.stderr):
                prediction = inference_skeleton(
                    model, poses, tuple(observations[0]["image_shape"])
                )
            scores = prediction.pred_score.detach().cpu().numpy()
            if scores.shape != (60,) or not np.isfinite(scores).all():
                raise ValueError("PoseC3D returned invalid NTU60 scores")
            competitor = max((i for i in range(60) if i != 47), key=lambda i: scores[i])
            result.update(
                status="scored",
                nausea_vomiting_score=float(scores[47]),
                competing_label=labels[competitor],
                competing_score=float(scores[competitor]),
                inference_seconds=round(time.perf_counter() - started, 3),
            )
        return result

    return score_window


def _score(args):
    score_window = load_scorer(args)
    count = 0
    with args.trace.open(encoding="utf-8") as trace:
        rows = (json.loads(line) for line in trace)
        for start_ms, end_ms, observations in windows(
            rows, args.window_seconds * 1000, args.stride_seconds * 1000, args.maximum_gap_ms
        ):
            result = score_window(start_ms, end_ms, observations)
            print(json.dumps(result, allow_nan=False), flush=True)
            count += 1
    if not count:
        raise ValueError("trace contains no complete windows")
    print(f"PoseC3D replay processed {count} complete windows", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--window-seconds", type=float, default=4)
    parser.add_argument("--stride-seconds", type=float, default=2)
    parser.add_argument("--maximum-gap-ms", type=float, default=500)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args(argv)
    if args.cpu_threads <= 0:
        parser.error("cpu threads must be positive")
    try:
        _score(args)
    except (ImportError, OSError, RuntimeError, AttributeError, TypeError,
            KeyError, IndexError, ValueError) as error:
        print(f"PoseC3D replay failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("PoseC3D replay interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
