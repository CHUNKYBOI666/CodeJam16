"""Run the detector on every clip in a folder and print a summary table.

    python vision/eval_clips.py                 # all videos in clips/
    python vision/eval_clips.py --dir local/takes --calibration local/calibration.json

Every frame is processed (no dropping) using media timestamps, so results are
deterministic and independent of laptop speed. "down->alert" is measured from the
first frame the detector classified as DOWN in that episode to the confirming frame.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from calibration import load_floor_polygon  # noqa: E402
from detector import PersonDownDetector, Posture  # noqa: E402
from pose import REPO_ROOT, load_model, pick_device, predict, primary_keypoints, warm_up  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def eval_clip(path: Path, model, device: str, calibration: str) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"clip": path.name, "error": "could not open"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    floor = load_floor_polygon(calibration, w, h)
    warm_up(model, device, w, h, passes=1)
    det = PersonDownDetector(floor_polygon=floor)
    alerts, frames, unknown, infer_ms = [], 0, 0, 0.0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        ts_ms = frames * 1000.0 / fps
        t0 = time.perf_counter()
        xy, conf = primary_keypoints(predict(model, img, device))
        infer_ms += (time.perf_counter() - t0) * 1000.0
        status = det.update(ts_ms, xy, conf)
        if status.reading.posture is Posture.UNKNOWN:
            unknown += 1
        if status.alert:
            alerts.append(status.alert)
        frames += 1
    cap.release()
    return {
        "clip": path.name,
        "seconds": frames / fps,
        "frames": frames,
        "unknown": unknown,
        "ms": infer_ms / max(frames, 1),
        "alerts": alerts,
        "floor": floor is not None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO_ROOT / "clips"))
    ap.add_argument("--calibration", default=str(REPO_ROOT / config.CALIBRATION_PATH))
    ap.add_argument("--weights")
    ap.add_argument("--device")
    args = ap.parse_args()

    clips = sorted(p for p in Path(args.dir).iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not clips:
        raise SystemExit(f"No video clips in {args.dir}")
    if not Path(args.calibration).exists():
        print(f"WARNING no calibration at {args.calibration}: whole frame counts as floor.\n")

    device = args.device or pick_device()
    model, model_name = load_model(args.weights)
    rows = []
    for p in clips:
        print(f"... {p.name}", flush=True)
        rows.append(eval_clip(p, model, device, args.calibration))

    header = f"{'clip':28s} {'len s':>6s} {'unk%':>5s} {'ms/f':>5s} {'alerts':>6s}  alert at (s) / down->alert (s)"
    print(f"\n{model_name} on {device}, confirm {config.CONFIRM_DOWN_SECONDS:.1f}s, "
          f"rearm {config.REARM_UPRIGHT_SECONDS:.1f}s, torso <= {config.DOWN_MAX_TORSO_DEG:.0f} deg\n")
    print(header)
    print("-" * len(header))
    for r in rows:
        if "error" in r:
            print(f"{r['clip']:28s} ERROR {r['error']}")
            continue
        detail = ", ".join(
            f"{a.detected_at_ms / 1000:.2f} / {(a.detected_at_ms - a.down_started_ms) / 1000:.2f}"
            for a in r["alerts"]
        ) or "-"
        unk = 100.0 * r["unknown"] / max(r["frames"], 1)
        print(f"{r['clip']:28s} {r['seconds']:6.1f} {unk:5.0f} {r['ms']:5.1f} {len(r['alerts']):6d}  {detail}")


if __name__ == "__main__":
    main()
