"""Probe a camera or clip without running pose inference (Continuity Camera setup check).

    python vision/capture_probe.py --source 0 --frames 120
    python vision/capture_probe.py --source clips/lying_down_twice.mp4 --frames 30 [--constant-fps 30]

Prints backend, frame count, size, timestamp range and whether the image changed.
Exit code 0 only if all requested frames arrived and the image changed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture import CaptureError, FrameSource  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="camera index (e.g. 0) or path to a video file")
    ap.add_argument("--frames", type=int, default=30, help="number of frames to sample (>= 2)")
    ap.add_argument("--snapshot", type=Path, help="save the first unannotated frame as a PNG")
    ap.add_argument("--constant-fps", type=float, help="files only: known constant frame rate")
    ap.add_argument("--stall-timeout", type=float, default=5.0, help="seconds without a frame before failing")
    args = ap.parse_args(argv)
    if args.frames < 2:
        ap.error("--frames must be at least 2")
    if args.snapshot is not None and args.snapshot.suffix.lower() != ".png":
        ap.error("--snapshot requires a .png path")
    source = int(args.source) if args.source.isdigit() else args.source

    count = 0
    changed = False
    first = None
    first_ms = last_ms = 0.0
    backend = "?"
    try:
        with FrameSource(
            source, realtime=False, constant_fps=args.constant_fps, stall_timeout_seconds=args.stall_timeout
        ) as src:
            initial_image = src.wait_ready()
            backend = src.backend
            if args.snapshot is not None:
                try:
                    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(args.snapshot), initial_image):
                        raise OSError("OpenCV could not write the PNG")
                except (OSError, cv2.error) as err:
                    print(f"Snapshot failed at {args.snapshot}: {err}", file=sys.stderr)
                    return 1
                print(f"Snapshot saved to {args.snapshot}")
            src.start()
            while count < args.frames:
                frame = src.latest()
                if frame is None:
                    break
                count += 1
                if first is None:
                    first, first_ms = frame.image, frame.ts_ms
                elif not changed:
                    changed = not np.array_equal(first, frame.image)
                last_ms = frame.ts_ms
    except (CaptureError, ValueError) as err:
        print(f"Capture failed after {count} frames: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    if first is None:
        print(f"Capture via {backend} returned no frames", file=sys.stderr)
        return 1
    h, w = first.shape[:2]
    print(
        f"backend={backend} frames={count}/{args.frames} dimensions={w}x{h} "
        f"timestamp_ms={first_ms:.3f}–{last_ms:.3f} changed={'yes' if changed else 'no'}"
    )
    return 0 if changed and count == args.frames else 1


if __name__ == "__main__":
    sys.exit(main())
