# CodeJam 16 — Live venue welfare checks

Hackathon project (McGill CodeJam 16, ~24h). Bias toward a working, visible demo over completeness.
The full design is in `docs/designs/live-venue-welfare-checks.md`; this file overrides it where they differ.

## Pitch (what we optimize for)
- **Agent Solution track (primary):** efficient compute + a demo that shows *how* it works.
  - Small local pose model, no per-frame cloud calls. Video never leaves the laptop.
  - Agent loop framing: perceive (pose) -> decide (timed posture rule) -> act (create/track check) -> human confirms.
  - Live compute meter: FPS, ms/frame, cloud calls (0 or 1 per incident), est. cost/hr vs. "send every frame to a VLM" baseline.
  - Feed overlay: skeleton, torso angle, "down X.Xs / 2.0s" timer bar that resets on recovery.
- **Health (secondary framing):** people with epilepsy, diabetes, POTS, heart conditions can go out on a Friday night knowing someone will notice if they go down. Label alerts "Possible person down" — staff confirm.
- **Privacy:** frames stay local, nothing stored, supervisor can see a skeleton-only view.

## Scope rules
- MUST: live detection + overlay, no alert on bend/sit/crouch, alert on sustained down, alert appears on map + phone, Accept -> Arrived -> Resolved (Confirmed / False alarm / Couldn't verify).
- KEEP (cheap): one-winner claim via `UPDATE ... WHERE owner IS NULL`, polling `/api/state` every ~1s.
- CUT: invite tokens/sessions (use a name picker + cookie), overdue badge, CI, deployment, exhaustive tests.
- STRETCH (only if core works): one Gemini call per incident for a one-line description.

## Stack
- Python 3.12 (NOT 3.14 — torch/ultralytics wheels lag). Use `uv` or a venv.
- `ultralytics` pose model: `yolo26n-pose.pt` (fall back to `yolo11n-pose.pt` if unavailable). `device="mps"` on Apple Silicon, else cpu.
- OpenCV for capture: same code path for webcam index or video file.
- Flask + SQLite + plain HTML/JS for staff views. One process; detector runs in a background thread.

## Detector contract
- COCO keypoints: shoulders 5,6; hips 11,12. Require conf >= 0.5 on all four, else frame = UNKNOWN.
- Down = torso (shoulder-mid -> hip-mid) within ~30 deg of horizontal AND midpoints in calibrated floor region.
- State machine: UPRIGHT -> PENDING (accumulate down time) -> ALERTED (latched) -> rearm only after 2.0s continuous upright. UNKNOWN breaks pending timers but never clears ALERTED. Use frame timestamps, not frame counts.
- On confirmation call `create_check(episode_id, signal="possible_person_down", zone, source, detected_at)`. Idempotent on episode_id.
- Thresholds live in `config.py`.
- Always process the latest frame only (drop stale frames).

## Conventions
- Keep detector logic pure (no cv2/IO) so it can be unit tested on fake keypoint sequences.
- Small commits. Run against `clips/` recordings before the live camera.
