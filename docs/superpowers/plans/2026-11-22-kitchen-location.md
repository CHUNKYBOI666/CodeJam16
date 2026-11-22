# Kitchen Fall-to-Minimap Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task by task. Preserve the existing worktree changes.

**Goal** — When the fixed iPhone detector confirms a person-down episode, show an approximate floor pin on that alert in the staff viewer.

**Architecture** — The browser exports the adjusted A–D corners from the verified camera frame. The Mac projects the confirmed person's torso midpoint through the saved floor transform and attaches optional coordinates to the existing alert. Supabase stores those fields on the check, and the viewer renders the pin against the wall and chair layout.

**Stack** — Python 3.12, OpenCV and YOLO26n-pose on the Mac, Supabase Postgres and Realtime, and a Vite frontend on Vercel.

**Design** — [Fixed-camera kitchen location](../specs/2026-11-22-kitchen-location-design.md)

## Starting point

- Work in `codex/kitchen-location-plan`. Review the working diff first. The location loader, detector hook, Supabase migration and viewer renderer already exist as local changes. Extend and verify them through the normal entry points.
- `local/kitchen-calibration-draft/stable-iphone.png` is the actual 1920 × 1080 detector capture. The local `stable-kitchen-minimap.html` has draggable A–D and a click simulator. Current edits are browser-local and do not reach Python.
- Source `0` opened the iPhone during the successful rehearsal. AVFoundation indexes can change on reconnect, even when both cameras return 1920 × 1080. Verify the displayed image each session.
- One person-down episode was confirmed on the overhead iPhone feed. The two-position rehearsal with mapped alerts and the hosted rollout remain open.

## Contracts

| Boundary | Contract |
| --- | --- |
| Editor → detector | Local JSON contains `points.image` as four normalized pairs in clockwise A–D order from the upper-left, `dimensions` as the actual source PNG's `[width,height]`, `camera_id` as `kitchen-iphone`, `source` as the verified numeric index and `map_version` as `kitchen-v2`. |
| Detector → Supabase | The existing alert JSON can include `map_x`, `map_y`, and `map_version` together. Both coordinates are numbers in `[0,1]`, with x increasing from wall to chair and y increasing from top to bottom of the saved view. |
| Supabase → viewer | A check retains its original coordinates through retry, claim, refresh, and resolution. The viewer draws the pin only when the coordinates are valid and its layout version matches. |

Use the visible **wall on the left** and **chair on the right** for orientation. Draw the open floor as a **3:2 rectangle**, with A–D tied to floor-level points and the tape mark near the center available as an independent check. Relative positions are sufficient. Keep the mounted framing, calibration PNG, private recordings, and the exported calibration on the Mac. Publish check metadata through the existing pipeline. Confirm only once per down episode and rearm after **two continuous seconds of clearly upright pose**.

## Task 1 — Export the adjusted corners

**Files** — Add `tools/kitchen-calibration.html` and `frontend/test/kitchen-calibration.browser.mjs`. Update the local draft and the calibration instructions in `README.md`.

- [ ] Add an export control to the current local editor so the operator can download their actual browser-saved A–D. Use the working coordinates at export time, divided by the SVG view width and height. Take `dimensions` from the source image's natural pixel dimensions. Write the contract above to `kitchen-minimap-setup.json`.
- [ ] Make a photo-free, checked-in editor from the same draggable workflow. Let the operator load a local PNG and import a saved setup deliberately. Reset the corners when the image changes. Lock the wall/chair layout to `kitchen-v2` and require a valid clockwise quadrilateral before export.
- [ ] Exercise `test("exports dragged corners at source-frame dimensions")`, using a 1920 × 1080 PNG displayed at a smaller size. Move A, export, and assert that A changes in the JSON, B–D stay fixed, and `dimensions` are `[1920,1080]`. Exercise `test("invalidates previous corners when replacing the frame")` and assert that Export stays unavailable until four valid corners are set.
- [ ] On the saved overhead frame, verify that the chosen A–D correspond to the intended floor rectangle. Use floor markings or a known floor pattern for the 3:2 proportions. Use the center tape mark and separate clicks on either side to check orientation. Save the verified export as `local/kitchen-minimap-setup.json`. Expected preview order is wall side, open floor, chair side.

## Task 2 — Use the export on a confirmed episode

**Files** — `vision/location.py`, `vision/run.py`, `tests/vision/test_location.py`, and `tests/vision/test_detector.py`.

- [ ] Keep `load_location_calibration(path, camera_id, source, frame_size) -> LocationCalibration` as the loader. Add a round-trip fixture produced by Task 1. Verify the four corner projections and the tape sample against the browser preview. The checker rejects crossed corners, wrong capture dimensions, wrong source/camera ID, or invalid map metadata.
- [ ] Guard live mapping against an index change or a crop with the same dimensions. Draw the saved A–D over the actual detector preview and show briefly how to enable location with **M**. Arm location only when the operator verifies the still-mounted view and presses **M** in that session. Live headless runs and sessions awaiting confirmation continue to send ordinary place-label alerts. A new capture session requires fresh confirmation.
- [ ] Keep projection on the confirming frame inside the existing `status.alert` branch in `vision/run.py`. Use the midpoint of the selected person's shoulder and hip midpoints via `LocationCalibration.map_fields(torso, frame_size)`. Send the same event to stdout and the existing background publisher. A point outside A–D or an unavailable calibration omits the map fields while still emitting the alert.
- [ ] Add `test_confirmed_episode_emits_its_selected_torso_location` covering two staged detector episodes with different points. Assert that pending frames emit no event, the first alert appears once, an interrupted upright interval does not rearm, and two continuous upright seconds allow a second alert at the second location. Verify that an unmapped confirming frame still yields a plain check.
- [ ] Run `.venv/bin/python -m pytest -q`. Verify one local upright/down/rearm cycle in the actual preview before adding `--publish`.

## Task 3 — Verify the stored alert and viewer

**Files** — `backend/supabase/migrations/20261122060724_kitchen_map.sql`, `backend/supabase/tests/hosted_checks.test.sql`, `backend/tests/race.mjs`, and `frontend/js/{kitchen-map,render,alert}.js`.

- [ ] Review the existing migration. All three map fields must be null or all three populated and valid. Ensure legacy ingest still works, an episode retry retains the initial pin, and only the existing service role can publish. Check that the map fields appear in the Realtime insert, room snapshot and claim RPC response.
- [ ] Extend the simultaneous claim test to use a mapped check. Assert exactly one winner and that both clients see the same responder and original map coordinates on a fresh snapshot. Check a legacy alert and a `kitchen-v1` event against the current viewer. Both should still show their place label.
- [ ] In a phone-sized browser, open a mapped check in the takeover, claim it, and refresh. Verify the wall remains on the left, the chair stays on the right, the pin stays in the same location in the check row, and the Accept control is visible.
- [ ] With the local stack started, run `(cd backend && supabase test db)`, `node backend/tests/race.mjs`, `npm test --prefix frontend`, `npm run build --prefix frontend`, and `git diff --check`. Record the results and any checks that required a local substitute for a hosted service.

## Task 4 — Rehearse and release

- [ ] Rehearse on the mounted iPhone against the local stack first. Use the verified source and run this command from the kitchen worktree. Review the A–D overlay and press **M** after confirming the image.

```bash
.venv/bin/python vision/run.py --source 0 --camera-id kitchen-iphone --zone kitchen \
  --location-calibration local/kitchen-minimap-setup.json --publish
```

- [ ] With the detector alone seeing the rehearsal area, stage one episode on the wall side. Remain clearly upright for at least two continuous seconds, then stage a second episode on the chair side. Each down episode should yield one check with a pin on its expected side. Claim from one joined phone and refresh on both joined phones to check the pin and assignee.
- [ ] On the linked demo project, apply the backwards-compatible Supabase migration first. Deploy the matching Vercel frontend second. Then point the Mac publisher at the demo project and repeat the two-position check through the live room QR. Protect the Mac-only publisher secret and use the existing publishable key in the viewer.
- [ ] Record the results in [the rehearsal log](../../hosted-rehearsal.md). If the angle or crop changes, use ordinary place-label alerts while the operator verifies and exports fresh corners. Keep `kitchen-v2` for the same floor layout and orientation. Change the layout version if the mapped floor coverage or orientation changes.

## Review focus

- Same-size laptop or recropped feed requires visual confirmation in the publishing session.
- A loaded 4:3 reference or an invalid projection must still allow a normal alert without a pin.
- A second alert must come from a new down episode after the existing rearm interval.
- Retried and simultaneous claims must preserve the first stored location and the winning responder.
- Old or unknown map versions must keep the responder workflow usable without a map.
