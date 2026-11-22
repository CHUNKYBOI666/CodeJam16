# Fixed-Camera Kitchen Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put an approximate, measured person-down position on a top-down kitchen map in each hosted welfare check.

**Architecture:** Keep the existing pose detector and event flow. Calibrate a fixed camera once with floor-level features whose positions are known on a kitchen layout, then apply an image-to-floor homography to the detected down person. Add optional location metadata to the existing Supabase check and render a map pin in the Vercel frontend.

**Tech Stack:** Python 3.12, pinned OpenCV and Numpy, Supabase Postgres and RPC, static Vite/JavaScript frontend.

**Spec:** [Fixed-Camera Kitchen Location Design](../specs/2026-11-22-kitchen-location-design.md)

## Global Constraints

- Start from merged hosted `main` commit `acdefe3`; this plan lives in the isolated `codex/kitchen-location-plan` worktree.
- The camera remains fixed for filming. Calibration is saved locally and reused at subsequent launches.
- Only floor-plane correspondences may enter the homography. A counter-top corner is not a floor point.
- Keep frames, calibration data, weights, reference stills and recordings out of Git; `local/` is already ignored.
- The browser receives layout geometry and event metadata only. The Python publisher alone uses the Supabase secret key.
- An absent or invalid location leaves the existing check workflow and place label usable.
- Keep the detector’s two-second down confirmation and two-second upright rearm rules unchanged.

## File responsibilities

| Path | Responsibility |
| --- | --- |
| `frontend/public/kitchen-layout.json` | Versioned measured footprint, counter polygon and floor-landmark coordinates; one source of truth for calibration and UI. |
| `vision/location.py` | Load/validate layout and calibration, estimate and validate the homography, project one down reading into metres and choose a nearby label. No capture or database I/O. |
| `vision/calibrate_map.py` | Pause on a camera frame, collect clicks for the configured landmarks, show validation, save `local/kitchen-calibration.json`. |
| `vision/run.py` | Load the map calibration, attach optional location to the existing alert event, and show a local diagnostic. |
| `backend/supabase/migrations/<CLI-generated>_kitchen_check_location.sql` | Nullable location columns, constraints, and `create_check` ingestion changes. |
| `frontend/js/kitchen-map.js` | Check/layout compatibility and safe SVG construction for room, counter and pin. |
| `frontend/js/main.js`, `frontend/js/render.js`, `frontend/index.html`, `frontend/styles.css` | Load the layout and display the pinned check in the room and alert takeover. |
| `README.md`, `docs/hosted-rehearsal.md` | Calibration, launch, and two-position rehearsal instructions/results. |

## Review Focus

- A counter-top point entered as a calibration landmark would distort the map: the setup instructions and validation must require floor-contact points.
- Four nearly collinear or closely clustered landmarks would give a plausible-looking wrong pin: calibration must reject a degenerate fit.
- A rotated, cropped or moved camera could reuse an old file: the runner must reject a mismatched camera ID, aspect ratio or layout version and warn locally.
- A pose near a wall or counter can project outside walkable floor: omit coordinates rather than clamp a misleading pin.
- An old or mock event has no location, and a saved check may reference an older layout: both should retain the text label with no pin.

---

### Task 1: Define and validate the measured kitchen layout

**Files:** Create `frontend/public/kitchen-layout.json` and `tests/vision/test_location.py`; create `vision/location.py` with layout parsing and validation.

**Interfaces:** `load_layout(path: str | Path) -> KitchenLayout` returns a typed layout with `version`, `width_m`, `height_m`, `room_polygon_m`, `counter_polygon_m`, and named `landmarks` (`id`, `label`, `(x_m, y_m)`). `validate_layout(layout: KitchenLayout) -> None` raises `ValueError` on bad geometry. Later tasks use these names and metre coordinates.

- [ ] **Step 1: Write failing layout tests.** Test a rectangular synthetic kitchen with a counter and five distinct floor landmarks. Assert ordered landmark coordinates, room bounds and version; reject duplicate IDs, out-of-room points, a zero-sized layout and a counter polygon with fewer than three points.
- [ ] **Step 2: Run the new tests.** `python3.12 -m pytest -q tests/vision/test_location.py`; expect failure because the layout API does not exist.
- [ ] **Step 3: Implement the parser and validation.** Keep the exact JSON fields listed above. Document that the room origin is the top-left of the top-down view, x grows right and y grows down. Use metres throughout. Commit a layout with the actual measured kitchen dimensions and the floor-contact locations of five or more fixed features; do not invent final measurements. A small synthetic layout may live only in the test fixture until the kitchen is measured.
- [ ] **Step 4: Run `python3.12 -m pytest -q tests/vision/test_location.py`.** Expect pass. Commit with `Add measured kitchen layout` when measurements are available.

### Task 2: Calibrate the fixed camera once

**Files:** Extend `vision/location.py`; create `vision/calibrate_map.py`; extend `tests/vision/test_location.py`.

**Interfaces:** `fit_homography(image_points_norm: Sequence[Point], floor_points_m: Sequence[Point]) -> CalibrationFit` returns a 3×3 matrix and error metrics. `save_map_calibration(path, camera_id, layout_version, source_size, image_points_norm, floor_points_m, matrix) -> None`; `load_map_calibration(path, camera_id, layout_version, frame_size) -> MapCalibration | None`. The CLI accepts `--source`, `--camera-id`, `--layout`, and `--output` (default `local/kitchen-calibration.json`).

- [ ] **Step 1: Write failing geometry tests.** A known perspective transform maps four floor corners to their metre coordinates and maps an interior point within 0.05 m. A fifth, held-out landmark checks the result. Reject fewer than five correspondences, collinear points, a singular matrix, a held-out error over 0.6 m, wrong camera ID, wrong layout version and materially changed aspect ratio. Accept the same image at half resolution with unchanged aspect ratio.
- [ ] **Step 2: Run the targeted geometry tests and confirm failure.**
- [ ] **Step 3: Implement the math and file format.** Normalize clicked image coordinates by source width/height. Fit on at least four well-spread points with `cv2.findHomography`; use a fifth point only for independent validation. Apply with `cv2.perspectiveTransform`. Save the original correspondences and fit diagnostics so calibration can be inspected. The CLI freezes one frame with `FrameSource`, asks for the landmarks in the layout’s listed order, supports undo/restart, shows the projected kitchen outline for review, and saves only after validation. Keep OpenCV UI and capture out of `vision/location.py`.
- [ ] **Step 4: Run `python3.12 -m pytest -q tests/vision/test_location.py` and a synthetic-frame CLI rehearsal.** Expect the geometry tests to pass and the tool to save a calibration file under ignored `local/`. Commit with `Add fixed-camera map calibration`.

### Task 3: Locate the confirmed down person without changing detection

**Files:** Extend `vision/location.py`, `vision/run.py`, and `tests/vision/test_location.py`; add a focused test in `tests/vision/test_detector.py` or `tests/vision/test_publish.py` for event enrichment.

**Interfaces:** `locate_down_reading(reading: PostureReading, calibration: MapCalibration, layout: KitchenLayout) -> Location | None` returns metre x/y and a label only for a valid `DOWN` reading inside walkable floor. `add_location(event: dict, location: Location | None, layout_version: str) -> dict` leaves the canonical fixture fields intact and adds `map_x_m`, `map_y_m`, `location_label`, `layout_version` only when location exists. `run.py` adds `--map-calibration`, defaulting to `local/kitchen-calibration.json`, and `--layout`, defaulting to the committed kitchen layout.

- [ ] **Step 1: Write failing tests.** A synthetic down reading at each of two known camera points maps to different positions and correct counter-side labels. An `UNKNOWN` or `UPRIGHT` reading, missing torso, invalid calibration or projected point on the counter/outside the room returns `None`. An event without location matches the old event shape exactly; one with location preserves `episode_id` and UTC timestamp.
- [ ] **Step 2: Run the targeted tests and confirm failure.**
- [ ] **Step 3: Implement the projection.** Use the average of shoulder and hip midpoints from the confirming down reading as the approximate floor contact point. Evaluate that point once per emitted episode, not on every frame. Pick “Near the counter” only when its distance to the counter footprint is within 1 m; otherwise use “Kitchen floor.” `run.py` logs whether mapping is active and why it was disabled. Keep the existing `--calibrate` floor-region detector setup and state machine intact.
- [ ] **Step 4: Run `python3.12 -m pytest -q` and replay a staged clip with `--no-show --every-frame` to inspect JSONL.** Expect one mapped event per down episode, and the old event shape if mapping is disabled. Commit with `Attach kitchen location to down alerts`.

### Task 4: Store optional location in Supabase

**Files:** Create a Supabase CLI-generated migration in `backend/supabase/migrations/`; extend `backend/supabase/tests/hosted_checks.test.sql`; adjust `fixtures/person-down.json` only if an example mapped event is useful (keep absent fields valid).

**Interfaces:** `public.create_check(p_event jsonb)` keeps its existing name and service-role-only execution. `checks` gains nullable `map_x_m numeric(8,3)`, `map_y_m numeric(8,3)`, `location_label text`, and `layout_version text`. A mapped check has all four; an unmapped check has none. `place` uses `location_label` when supplied, otherwise the existing zone/default setting.

- [ ] **Step 1: Add failing pgTAP cases.** Ingest a mapped event and assert coordinates, label and version on the saved check and `room_snapshot`; send the same `episode_id` with different coordinates and assert the original saved location remains. Ingest an old event and assert no coordinates and the existing default place. Reject half-populated location, nonnumeric coordinates and a label longer than 80 characters. Keep the unauthorized-browser and claim-race checks.
- [ ] **Step 2: Run `cd backend && supabase test db`; confirm the new cases fail.** Discover CLI syntax with `supabase migration new --help`, then generate the migration with `supabase migration new kitchen_check_location`; do not hand-name a migration timestamp.
- [ ] **Step 3: Add nullable columns and a four-field all-or-none check; update `create_check` only at initial insert.** Give coordinates a reasonable nonnegative upper bound (under 1000 m) and keep existing RLS/select grants and service-role-only RPC grants. Do not expose a new browser write path or a camera frame.
- [ ] **Step 4: Run `cd backend && supabase db reset && supabase test db`, then `node backend/tests/race.mjs`.** Expect all existing and new tests to pass. Commit with `Store optional kitchen check locations`.

### Task 5: Render the kitchen pin on joined phones

**Files:** Create `frontend/js/kitchen-map.js` and `frontend/test/kitchen-map.test.js`; modify `frontend/js/main.js`, `frontend/js/render.js`, `frontend/index.html`, and `frontend/styles.css`.

**Interfaces:** `locationForLayout(check, layout) -> {x_m, y_m, label} | null` returns `null` if any coordinate is missing, nonfinite, out of bounds or the layout version differs. `renderKitchenMap(check, layout) -> SVGElement | null` draws the room, counter and pin with DOM/SVG APIs. `main.js` fetches the layout once and supplies it to check and takeover rendering.

- [ ] **Step 1: Write failing Node tests.** Assert correct conversion of metre coordinates into an SVG `viewBox`, safe omission for old/mock events, and omission when a check’s layout version differs. Check that arbitrary labels stay text, never parsed as markup.
- [ ] **Step 2: Run `npm test --prefix frontend`; confirm new cases fail.**
- [ ] **Step 3: Implement a compact map in the active check row and takeover.** Use the layout footprint and counter polygon, a visible pin, `role="img"` with an accessible location label, and `textContent` for event labels. Keep the existing `place` text. Do not show a blank map when the layout fetch fails or when location is absent. Ensure the map works at phone width and remains readable in light and dark themes.
- [ ] **Step 4: Run `npm test --prefix frontend` and `npm run build --prefix frontend`.** Check an active mapped check and an unmapped mock check in a mobile-width browser. Commit with `Show kitchen positions in hosted checks`.

### Task 6: Rehearse the complete kitchen flow

**Files:** Modify `README.md` and `docs/hosted-rehearsal.md`; adjust implementation/tests only for failures observed in this rehearsal.

- [ ] **Step 1: Add exact operator commands and setup steps.** Document mounting the camera, drawing/measuring the top-down layout, choosing five floor-level landmarks, running the existing floor-region calibration, running `vision/calibrate_map.py`, checking the saved reprojection error, and launching `vision/run.py --source <camera-index> --publish --map-calibration local/kitchen-calibration.json`.
- [ ] **Step 2: From the final camera position, mark two known floor spots on the kitchen layout and stage a down event at each.** Keep the camera mounted. Stand clearly upright for at least 2 continuous seconds between episodes. Compare both pins to measured spots; target within approximately 1 m and the correct side of the counter. If the pose or camera view fails, adjust the setup and recalibrate before judging the math.
- [ ] **Step 3: Verify the hosted path on two joined phones.** Confirm the two checks keep the same pins and labels after refresh, claim, arrival and resolution. Confirm the old mock command still creates a text-only check. Record screenshot/short clip and measured errors in `docs/hosted-rehearsal.md` without committing raw camera media or credentials.
- [ ] **Step 4: Run final gates.** `python3.12 -m pytest -q`, `npm test --prefix frontend`, `npm run build --prefix frontend`, `cd backend && supabase test db`, `node backend/tests/race.mjs`, and `git diff --check`. Only then prepare a PR with what was verified and a screenshot or short recording.

## Execution note

Tasks 1–3 can be developed and tested locally without Supabase access. Task 4 changes the hosted schema; deploy it before publishing mapped events. Task 5 is compatible with old checks. The measured kitchen layout and final camera still are required to complete Task 1 and Task 6, but the synthetic geometry tests can be built first.
