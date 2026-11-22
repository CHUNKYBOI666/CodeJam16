"""Probe local video or a macOS camera without running pose inference."""

import argparse
from itertools import islice
import sys

import numpy as np

from capture import CaptureError, open_camera, open_replay


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="local rehearsal clip")
    source.add_argument("--camera-index", type=int, help="macOS camera device index")
    parser.add_argument("--frames", type=int, default=30, help="number of frames to sample")
    parser.add_argument("--constant-fps", type=float, help="known constant frame rate of the clip")
    args = parser.parse_args(argv)
    if args.frames < 2:
        parser.error("--frames must be at least 2")
    if args.camera_index is not None and args.constant_fps is not None:
        parser.error("--constant-fps requires --file")

    count = 0
    changed = False
    first_frame = None
    try:
        source_context = (
            open_replay(args.file, constant_fps=args.constant_fps)
            if args.file is not None
            else open_camera(args.camera_index)
        )
        with source_context as frames:
            backend = frames.backend
            for sample in islice(frames, args.frames):
                count += 1
                if first_frame is None:
                    first_frame = sample.frame_bgr
                    first_ms = sample.timestamp_ms
                elif not changed:
                    changed = not np.array_equal(first_frame, sample.frame_bgr)
                last_ms = sample.timestamp_ms
    except (CaptureError, ValueError) as error:
        print(f"Capture failed after {count} frames: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    if first_frame is None:
        print(f"Capture via {backend} returned no frames", file=sys.stderr)
        return 1
    height, width = first_frame.shape[:2]
    print(
        f"backend={backend} frames={count}/{args.frames} dimensions={width}x{height} "
        f"timestamp_ms={first_ms:.3f}–{last_ms:.3f} changed={'yes' if changed else 'no'}"
    )
    return 0 if changed and count == args.frames else 1


if __name__ == "__main__":
    sys.exit(main())
