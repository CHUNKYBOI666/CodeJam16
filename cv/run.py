"""Live pose overlay: skeleton, torso angle, FPS and ms/frame on a webcam or video file.

    python cv/run.py --source 0                     # webcam index
    python cv/run.py --source clips/lie_down.mp4    # video file, paced at real time
    python cv/run.py --source clips/lie_down.mp4 --no-show --save local/out.mp4

The capture thread keeps only the newest frame; inference always runs on that one and
stale frames are dropped (counted on the overlay). Video files are paced at their own
frame rate so dropping behaves like a live camera. Use --every-frame to process every
frame of a file instead (deterministic, not real time).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from pose_math import Torso, torso_from_keypoints  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- capture


@dataclass
class Frame:
    image: np.ndarray
    ts_ms: float  # media time for files, elapsed capture time for live
    seq: int


class LatestFrameReader:
    """Background reader that holds only the most recent frame."""

    def __init__(self, source: int | str, realtime: bool = True):
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise SystemExit(f"Could not open source: {source!r}")
        self.is_file = isinstance(source, str)
        self.realtime = realtime or not self.is_file
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.source_fps = fps if fps and fps > 1 else 30.0
        self.dropped = 0
        self.eof = False
        self._latest: Frame | None = None
        self._consumed = True
        self._cond = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        start = time.monotonic()
        idx = 0
        while not self._stop:
            ok, image = self.cap.read()
            if not ok:
                break
            if self.is_file:
                ts_ms = idx * 1000.0 / self.source_fps
                if self.realtime:
                    delay = start + ts_ms / 1000.0 - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
            else:
                ts_ms = (time.monotonic() - start) * 1000.0
            with self._cond:
                if not self.realtime:
                    # --every-frame: wait for the consumer instead of dropping.
                    self._cond.wait_for(lambda: self._consumed or self._stop)
                elif not self._consumed:
                    self.dropped += 1
                self._latest = Frame(image, ts_ms, idx)
                self._consumed = False
                self._cond.notify_all()
            idx += 1
        with self._cond:
            self.eof = True
            self._cond.notify_all()

    def latest(self, timeout: float = 1.0) -> Frame | None:
        """Block until an unseen frame is available; None at end of stream / timeout."""
        with self._cond:
            self._cond.wait_for(lambda: not self._consumed or self.eof, timeout=timeout)
            if self._consumed:
                return None
            self._consumed = True
            self._cond.notify_all()
            return self._latest

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        self._thread.join(timeout=2)
        self.cap.release()


# ---------------------------------------------------------------- model


def pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(weights: str | None):
    from ultralytics import YOLO

    weights_dir = REPO_ROOT / config.WEIGHTS_DIR
    weights_dir.mkdir(parents=True, exist_ok=True)
    candidates = [weights] if weights else list(config.MODEL_CANDIDATES)
    last_err = None
    for name in candidates:
        path = Path(name)
        if not path.is_absolute() and path.parent == Path("."):
            path = weights_dir / name
        try:
            model = YOLO(str(path))
            print(f"[run] loaded {path.name}")
            return model, path.name
        except Exception as err:  # weights unavailable -> try the next candidate
            print(f"[run] could not load {name}: {err}")
            last_err = err
    raise SystemExit(f"Detector unavailable: no pose weights loaded ({last_err})")


def primary_person(result) -> int | None:
    """Index of the most confident person, or None if nobody was detected."""
    if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
        return None
    return int(result.boxes.conf.argmax().item())


# ---------------------------------------------------------------- overlay

GREEN = (80, 220, 80)
AMBER = (0, 190, 255)
GREY = (170, 170, 170)
WHITE = (255, 255, 255)


def draw_panel(img, lines: list[tuple[str, tuple[int, int, int]]]) -> None:
    k = max(1.0, min(img.shape[:2]) / 540.0)  # scale text up for large frames
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.6 * k, max(2, int(2 * k))
    pad, line_h = int(10 * k), int(24 * k)
    width = max(cv2.getTextSize(t, font, scale, thick)[0][0] for t, _ in lines) + 2 * pad
    height = line_h * len(lines) + pad
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (width, height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, (text, color) in enumerate(lines):
        y = pad + line_h * (i + 1) - 6
        cv2.putText(img, text, (pad, y), font, scale, color, thick, cv2.LINE_AA)


def draw_torso(img, torso: Torso) -> None:
    down = torso.angle_deg <= config.DOWN_MAX_TORSO_DEG
    color = AMBER if down else GREEN
    k = max(1.0, min(img.shape[:2]) / 540.0)
    s = tuple(int(v) for v in torso.shoulder_mid)
    h = tuple(int(v) for v in torso.hip_mid)
    cv2.line(img, s, h, color, int(4 * k), cv2.LINE_AA)
    cv2.circle(img, s, int(6 * k), color, -1)
    cv2.circle(img, h, int(6 * k), color, -1)
    label = f"{torso.angle_deg:.0f} deg"
    org = (h[0] + int(10 * k), h[1])
    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * k, (0, 0, 0), int(4 * k), cv2.LINE_AA)
    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * k, color, max(2, int(2 * k)), cv2.LINE_AA)


# ---------------------------------------------------------------- main


def parse_source(raw: str) -> int | str:
    if raw.isdigit():
        return int(raw)
    if not Path(raw).exists():
        raise SystemExit(f"Source not found: {raw}")
    return raw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="webcam index (e.g. 0) or path to a video file")
    ap.add_argument("--weights", help="override pose weights (default: yolo26n-pose, fallback yolo11n-pose)")
    ap.add_argument("--device", help="mps / cuda / cpu (default: auto)")
    ap.add_argument("--every-frame", action="store_true", help="files only: process every frame, no dropping")
    ap.add_argument("--no-show", action="store_true", help="do not open a window")
    ap.add_argument("--save", help="write the annotated video to this path")
    ap.add_argument("--log-every", type=float, default=1.0, help="seconds between console stat lines (0 = off)")
    args = ap.parse_args()

    source = parse_source(args.source)
    device = args.device or pick_device()
    model, model_name = load_model(args.weights)

    # Warm up so the first real frame's latency isn't dominated by graph setup.
    model.predict(np.zeros((480, 640, 3), np.uint8), device=device, imgsz=config.INFERENCE_IMGSZ, verbose=False)

    reader = LatestFrameReader(source, realtime=not args.every_frame)
    writer = None
    fps_ema = None
    ms_ema = None
    last_wall = None
    last_log = time.monotonic()
    processed = 0
    angles: list[float] = []
    unknown = 0
    t_start = time.monotonic()

    try:
        while True:
            frame = reader.latest()
            if frame is None:
                if reader.eof:
                    break
                continue

            t0 = time.perf_counter()
            result = model.predict(
                frame.image,
                device=device,
                imgsz=config.INFERENCE_IMGSZ,
                conf=config.PERSON_MIN_CONF,
                verbose=False,
            )[0]
            infer_ms = (time.perf_counter() - t0) * 1000.0

            now = time.monotonic()
            if last_wall is not None:
                inst_fps = 1.0 / max(now - last_wall, 1e-6)
                fps_ema = inst_fps if fps_ema is None else 0.9 * fps_ema + 0.1 * inst_fps
            last_wall = now
            ms_ema = infer_ms if ms_ema is None else 0.9 * ms_ema + 0.1 * infer_ms
            processed += 1

            img = result.plot(boxes=False, labels=False, conf=False)  # skeleton(s)
            torso = None
            person = primary_person(result)
            if person is not None:
                kp = result.keypoints
                xy = kp.xy[person].cpu().numpy()
                conf = kp.conf[person].cpu().numpy() if kp.conf is not None else None
                torso = torso_from_keypoints(xy, conf)

            if torso is not None:
                draw_torso(img, torso)
                angles.append(torso.angle_deg)
                posture = ("DOWN-ish", AMBER) if torso.angle_deg <= config.DOWN_MAX_TORSO_DEG else ("upright", GREEN)
                torso_line = (f"torso {torso.angle_deg:5.1f} deg  ({posture[0]})", posture[1])
            else:
                unknown += 1
                torso_line = ("torso UNKNOWN (no person / low conf)", GREY)

            draw_panel(
                img,
                [
                    (f"{model_name} on {device}", WHITE),
                    (f"FPS {fps_ema or 0:5.1f}   {ms_ema:5.1f} ms/frame", WHITE),
                    (torso_line[0], torso_line[1]),
                    (f"t={frame.ts_ms / 1000:6.2f}s  dropped {reader.dropped}  cloud calls 0", GREY),
                ],
            )

            if args.save:
                if writer is None:
                    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
                    h, w = img.shape[:2]
                    out_fps = reader.source_fps if args.every_frame else max(fps_ema or 15.0, 1.0)
                    writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h))
                writer.write(img)

            if not args.no_show:
                cv2.imshow("CodeJam16 pose", img)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break

            if args.log_every and now - last_log >= args.log_every:
                last_log = now
                ang = f"{torso.angle_deg:5.1f}" if torso else "  UNK"
                print(
                    f"[run] t={frame.ts_ms / 1000:6.2f}s fps={fps_ema or 0:5.1f} "
                    f"ms={infer_ms:5.1f} torso={ang} dropped={reader.dropped}"
                )
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        if writer is not None:
            writer.release()
        if not args.no_show:
            cv2.destroyAllWindows()

    wall = time.monotonic() - t_start
    print(
        f"[run] done: {processed} frames processed in {wall:.1f}s "
        f"({processed / max(wall, 1e-6):.1f} fps), {reader.dropped} dropped, "
        f"avg {ms_ema or 0:.1f} ms/frame (EMA), torso UNKNOWN on {unknown} frames"
    )
    if angles:
        print(f"[run] torso angle min {min(angles):.1f} / max {max(angles):.1f} deg")
    if args.save:
        print(f"[run] saved {args.save}")


if __name__ == "__main__":
    main()
