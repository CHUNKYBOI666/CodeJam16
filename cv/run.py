"""Live person-down detector with overlay: skeleton, torso angle, floor region, timers, FPS.

    python cv/run.py --source 0                     # webcam index
    python cv/run.py --source clips/lying_down_twice.mp4    # video file, paced at real time
    python cv/run.py --source clips/lying_down_twice.mp4 --no-show --save local/out.mp4
    python cv/run.py --source 0 --calibrate         # click the floor polygon on the first frame

Output: alerts go to stdout as one JSON object per line (fields as in
fixtures/person-down.json); all logs, including library output, go to stderr.

    python cv/run.py --source 0 --no-show > local/alerts.jsonl

Capture (cv/capture.py) keeps only the newest frame; inference always runs on that one
and stale frames are dropped (counted on the overlay). Video files are paced at their
media time so dropping behaves like a live camera. Use --every-frame to process every
frame of a file instead (deterministic, not real time). Exit codes: 2 if the camera
stops delivering frames for --stall-timeout seconds or the clip can't be decoded,
3 if whatever reads stdout goes away.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from calibration import load_floor_polygon, save_calibration  # noqa: E402
from capture import CaptureError, FrameSource  # noqa: E402
from detector import PersonDownDetector, Posture, State, Status, alert_event  # noqa: E402
from pose import REPO_ROOT, load_model, pick_device, people_from_result, predict, warm_up  # noqa: E402


# ---------------------------------------------------------------- calibration UI


def run_calibration(image: np.ndarray, out_path: Path, source_label: str) -> bool:
    """Click a floor polygon on the frame. Enter/s saves, u undoes, c clears, q/Esc cancels."""
    h, w = image.shape[:2]
    scale = min(1.0, 1000.0 / w, 900.0 / h)
    base = cv2.resize(image, (int(w * scale), int(h * scale))) if scale < 1 else image.copy()
    points: list[tuple[float, float]] = []  # full-resolution pixel coords

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x / scale, y / scale))

    win = "Calibrate floor region"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)
    help_text = "click floor corners | Enter/s save | u undo | c clear | q cancel"
    while True:
        view = base.copy()
        pts = np.array([(int(x * scale), int(y * scale)) for x, y in points], np.int32)
        if len(pts) >= 3:
            fill = view.copy()
            cv2.fillPoly(fill, [pts], (255, 200, 0))
            cv2.addWeighted(fill, 0.3, view, 0.7, 0, view)
        if len(pts) >= 2:
            cv2.polylines(view, [pts], len(pts) >= 3, (255, 200, 0), 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(view, tuple(int(v) for v in p), 5, (0, 255, 255), -1)
        cv2.rectangle(view, (0, 0), (view.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(view, f"{help_text}  [{len(points)} pts]", (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(win, view)
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 10, ord("s")):
            if len(points) < 3:
                print("[calibrate] need at least 3 points")
                continue
            save_calibration(out_path, points, w, h, source_label)
            print(f"[calibrate] saved {len(points)}-point floor polygon to {out_path}")
            cv2.destroyWindow(win)
            return True
        if key in (ord("u"), 8, 127) and points:
            points.pop()
        elif key == ord("c"):
            points.clear()
        elif key in (ord("q"), 27):
            print("[calibrate] cancelled, nothing saved")
            cv2.destroyWindow(win)
            return False


# ---------------------------------------------------------------- overlay

GREEN = (80, 220, 80)
AMBER = (0, 190, 255)
RED = (40, 40, 230)
CYAN = (255, 200, 0)
GREY = (170, 170, 170)
WHITE = (255, 255, 255)


def ui_scale(img) -> float:
    return max(1.0, min(img.shape[:2]) / 540.0)


def draw_panel(img, lines: list[tuple[str, tuple[int, int, int]]]) -> int:
    """Draw a text panel top-left; returns its bottom y."""
    k = ui_scale(img)
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
    return height


def draw_bar(img, y: int, label: str, frac: float, color) -> int:
    k = ui_scale(img)
    pad, bar_w, bar_h = int(10 * k), int(260 * k), int(16 * k)
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.55 * k, max(2, int(2 * k))
    y += pad
    overlay = img.copy()
    cv2.rectangle(overlay, (0, y - pad // 2), (bar_w + 2 * pad, y + bar_h + int(26 * k)), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    cv2.putText(img, label, (pad, y + int(14 * k)), font, scale, color, thick, cv2.LINE_AA)
    y += int(20 * k)
    cv2.rectangle(img, (pad, y), (pad + bar_w, y + bar_h), GREY, max(1, int(k)))
    cv2.rectangle(img, (pad, y), (pad + int(bar_w * max(0.0, min(1.0, frac))), y + bar_h), color, -1)
    return y + bar_h


def draw_floor(img, polygon) -> None:
    if not polygon:
        return
    pts = np.array([(int(x), int(y)) for x, y in polygon], np.int32)
    cv2.polylines(img, [pts], True, CYAN, max(2, int(2 * ui_scale(img))), cv2.LINE_AA)


def draw_torso(img, status: Status) -> None:
    r = status.reading
    if r.torso is None:
        return
    k = ui_scale(img)
    color = AMBER if r.posture is Posture.DOWN else GREEN
    if r.bbox is not None:
        x0, y0, x1, y1 = (int(v) for v in r.bbox)
        cv2.rectangle(img, (x0, y0), (x1, y1), color, max(1, int(k)))
    s = tuple(int(v) for v in r.torso.shoulder_mid)
    h = tuple(int(v) for v in r.torso.hip_mid)
    cv2.line(img, s, h, color, int(4 * k), cv2.LINE_AA)
    cv2.circle(img, s, int(6 * k), color, -1)
    cv2.circle(img, h, int(6 * k), color, -1)
    label = f"{r.torso.angle_deg:.0f} deg"
    org = (h[0] + int(10 * k), h[1])
    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * k, (0, 0, 0), int(4 * k), cv2.LINE_AA)
    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * k, color, max(2, int(2 * k)), cv2.LINE_AA)


def draw_alert_banner(img) -> None:
    k = ui_scale(img)
    h, w = img.shape[:2]
    bh = int(50 * k)
    cv2.rectangle(img, (0, h - bh), (w, h), RED, -1)
    text = "POSSIBLE PERSON DOWN"
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.9 * k, max(2, int(2 * k))
    tw = cv2.getTextSize(text, font, scale, thick)[0][0]
    cv2.putText(img, text, ((w - tw) // 2, h - bh // 3), font, scale, WHITE, thick, cv2.LINE_AA)


def annotate(img, status: Status, floor, model_name, device, fps, ms, ts_ms, dropped) -> None:
    r = status.reading
    draw_floor(img, floor)
    draw_torso(img, status)
    if r.torso is not None:
        tcol = AMBER if r.torso.angle_deg <= config.DOWN_MAX_TORSO_DEG else GREEN
        torso_line = (f"torso {r.torso.angle_deg:5.1f} deg", tcol)
        aspect = "inf" if r.bbox_aspect == float("inf") else f"{r.bbox_aspect or 0:.2f}"
        geo_line = (f"box w/h {aspect}   on floor {'yes' if r.in_floor else 'no'}", GREY)
    else:
        torso_line = (f"torso UNKNOWN ({r.reason.value if r.reason else '-'})", GREY)
        geo_line = ("box w/h -   on floor -", GREY)
    state_color = {State.UPRIGHT: GREEN, State.PENDING: AMBER, State.ALERTED: RED}[status.state]
    bottom = draw_panel(
        img,
        [
            (f"{model_name} on {device}", WHITE),
            (f"FPS {fps:5.1f}   {ms:5.1f} ms/frame", WHITE),
            torso_line,
            geo_line,
            (f"state {status.state.value}  ({r.reason.value if r.reason else r.posture.value})", state_color),
            (f"t={ts_ms / 1000:6.2f}s  people {r.people}  dropped {dropped}  cloud calls 0", GREY),
        ],
    )
    confirm = config.CONFIRM_DOWN_SECONDS
    if status.state is State.ALERTED:
        bottom = draw_bar(img, bottom, f"down {status.down_seconds:4.1f}s (alerted)", 1.0, RED)
        rearm = config.REARM_UPRIGHT_SECONDS
        draw_bar(img, bottom, f"upright {status.rearm_seconds:3.1f}s / {rearm:.1f}s to rearm",
                 status.rearm_seconds / rearm, GREEN)
        draw_alert_banner(img)
    else:
        draw_bar(img, bottom, f"down {status.down_seconds:3.1f}s / {confirm:.1f}s",
                 status.down_seconds / confirm, AMBER)


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
    ap.add_argument("--calibrate", action="store_true", help="click the floor polygon on the first frame and exit")
    ap.add_argument("--calibration", default=str(REPO_ROOT / config.CALIBRATION_PATH), help="calibration json path")
    ap.add_argument("--camera-id", default=config.CAMERA_ID, help="camera id reported with alerts")
    ap.add_argument("--zone-id", "--zone", dest="zone_id", default=config.ZONE_ID, help="zone id reported with alerts")
    ap.add_argument("--weights", help="override pose weights (default: yolo26n-pose, fallback yolo11n-pose)")
    ap.add_argument("--device", help="mps / cuda / cpu (default: auto)")
    ap.add_argument("--every-frame", action="store_true", help="files only: process every frame, no dropping")
    ap.add_argument("--constant-fps", type=float, help="files only: use index/fps timestamps instead of media time")
    ap.add_argument("--stall-timeout", type=float, default=5.0, help="seconds without a camera frame = feed lost")
    ap.add_argument("--no-show", action="store_true", help="do not open a window")
    ap.add_argument("--save", help="write the annotated video to this path")
    ap.add_argument("--log-every", type=float, default=1.0, help="seconds between console stat lines (0 = off)")
    args = ap.parse_args()

    # stdout carries alerts only, one JSON object per line. Everything else, including
    # library output (ultralytics is imported later), goes to stderr.
    alert_out = sys.stdout
    sys.stdout = sys.stderr

    source = parse_source(args.source)
    try:
        reader = FrameSource(
            source,
            realtime=not args.every_frame,
            constant_fps=args.constant_fps,
            stall_timeout_seconds=args.stall_timeout,
        )
        reader.wait_ready()
    except (CaptureError, ValueError) as err:
        raise SystemExit(f"[run] CAPTURE FAILED: {err}")
    print(f"[run] source {args.source} via {reader.backend}, {reader.width}x{reader.height}")

    if args.calibrate:
        run_calibration(reader.first_frame, Path(args.calibration), str(args.source))
        reader.close()
        return

    floor = load_floor_polygon(args.calibration, reader.width, reader.height)
    if floor is None:
        print(f"[run] WARNING no calibration at {args.calibration}: whole frame counts as floor. "
              f"Run with --calibrate first.")
    else:
        print(f"[run] floor polygon: {len(floor)} points from {args.calibration}")

    device = args.device or pick_device()
    model, model_name = load_model(args.weights)
    t_warm = time.perf_counter()
    warm_up(model, device, reader.width, reader.height)
    print(f"[run] warmed up at {reader.width}x{reader.height} in {time.perf_counter() - t_warm:.1f}s")

    detector = PersonDownDetector(floor_polygon=floor, frame_size=(reader.width, reader.height))
    source_kind = "replay" if reader.is_file else "live"
    reader.start()  # capture (and file pacing) begins only after the model is warm

    writer = None
    fps_ema = None
    ms_ema = None
    last_wall = None
    last_log = time.monotonic()
    processed = 0
    alerts = 0
    t_start = time.monotonic()
    capture_error = None
    alert_pipe_closed = False

    try:
        while True:
            try:
                frame = reader.latest()
            except CaptureError as err:
                # Camera offline / clip unreadable. A latched episode stays latched; the
                # caller (staff app integration) decides whether to fall back to replay.
                capture_error = err
                print(f"[run] CAPTURE FAILED ({type(err).__name__}): {err}")
                break
            if frame is None:  # end of file
                break

            t0 = time.perf_counter()
            result = predict(model, frame.image, device)
            infer_ms = (time.perf_counter() - t0) * 1000.0
            status = detector.update_people(frame.ts_ms, people_from_result(result))

            now = time.monotonic()
            if last_wall is not None:
                inst_fps = 1.0 / max(now - last_wall, 1e-6)
                fps_ema = inst_fps if fps_ema is None else 0.9 * fps_ema + 0.1 * inst_fps
            last_wall = now
            ms_ema = infer_ms if ms_ema is None else 0.9 * ms_ema + 0.1 * infer_ms
            processed += 1

            if status.alert:
                alerts += 1
                a = status.alert
                event = alert_event(
                    a,
                    camera_id=args.camera_id,
                    zone_id=args.zone_id,
                    source=source_kind,
                    detected_at=datetime.now(timezone.utc),
                )
                print(f"[ALERT] {a.episode_id} t={a.detected_at_ms / 1000:.2f}s "
                      f"(down since {a.down_started_ms / 1000:.2f}s, "
                      f"+{(a.detected_at_ms - a.down_started_ms) / 1000:.2f}s)")
                try:
                    alert_out.write(json.dumps(event) + "\n")
                    alert_out.flush()
                except BrokenPipeError:
                    alert_pipe_closed = True
                    print("[run] alert consumer closed stdout; stopping")
                    break

            img = result.plot(boxes=False, labels=False, conf=False)  # skeleton(s)
            annotate(img, status, floor, model_name, device, fps_ema or 0.0, ms_ema, frame.ts_ms, reader.dropped)

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
                r = status.reading
                ang = f"{r.torso.angle_deg:5.1f}" if r.torso else "  UNK"
                why = f" ({r.reason.value})" if r.reason else ""
                print(
                    f"[run] t={frame.ts_ms / 1000:6.2f}s fps={fps_ema or 0:5.1f} ms={infer_ms:5.1f} "
                    f"torso={ang} {status.state.value:8s}{why} down={status.down_seconds:4.1f}s "
                    f"people={r.people} dropped={reader.dropped}"
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
        f"avg {ms_ema or 0:.1f} ms/frame (EMA), {alerts} alert(s)"
    )
    if args.save:
        print(f"[run] saved {args.save}")
    if capture_error is not None:
        raise SystemExit(2)
    if alert_pipe_closed:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
