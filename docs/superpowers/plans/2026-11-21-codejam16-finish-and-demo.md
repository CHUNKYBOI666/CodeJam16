# CodeJam16 Finish-and-Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get all existing work onto `main`, connect camera → detector → incident store → staff screens, prove it with tests, detector evidence and rehearsals, and demo it in 18 hours.

**Architecture:** One Flask process on the Mac. A daemon worker thread runs `PersonDownPipeline` (capture + YOLO26n-pose + `PersonDownDetector`) and delivers confirmed events to `IncidentStore.create_incident`. A thread-safe `FeedState` object is shared with `/api/state`. `config/venue.json` is the single source of truth for zones, map pins and detector thresholds.

**Tech Stack:** Python 3.12 (`uv`), Flask 3, SQLite, OpenCV, Ultralytics YOLO26n-pose, `unittest`, vanilla JS.

**Spec:** [docs/superpowers/specs/2026-11-21-codejam16-finish-and-demo-design.md](../specs/2026-11-21-codejam16-finish-and-demo-design.md). Product behaviour: [engineering plan](../../designs/live-venue-welfare-checks.md). UI: [locked mockup](../../mockups/staff-app.html). Demo script and fallback table: [BUILD-MAP §5](../../BUILD-MAP.md).

## Global Constraints

- Python 3.12 via `uv`. Pins: `Flask>=3.0,<4.0`, `ultralytics==8.4.216`, `opencv-python==5.1.0.95`, `torch==2.15.0`, `torchvision==0.29.2`. PoseC3D deps go only in `requirements-posec3d.txt`.
- Test command (must stay green on every merge): `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`. No test may load YOLO weights or open a camera.
- Zone IDs: `east-bar` (East bar · Beside the side stairs, the live camera) and `main-floor` (Main floor · Near the north tables, labelled sample only).
- Signal label: **Possible person down**. Outcomes, exact strings: `Confirmed person down`, `False alarm`, `Couldn't verify`.
- Statuses `needs_responder → accepted → arrived → resolved`. UI words: "Needs responder", **"Being resolved"** (accepted and arrived, teal), **"Closed"** (resolved, history only).
- Feed statuses: `live`, `replay`, `offline`, `detector_unavailable`, `finished`, plus the existing `rehearsal` for `--no-detector`.
- Timing values are named with units (`confirmation_seconds`, `stall_timeout_seconds`). Timestamps are UTC ISO with `Z`.
- `local/` holds clips, weights, databases and traces, and is never committed. Footage uses consenting teammates only.
- Branches: one per task off `main`, under about 300 lines. `git fetch && git rebase origin/main` before pushing. Open a PR, get one reviewer, squash-merge, delete the branch.
- Shared files are owned by C: `README.md`, `requirements*.txt`, `config/venue.json`, `fixtures/person-down.json`.
- H12 code freeze. After it, only rehearsal-bug fixes (each with a test) and `config/venue.json` threshold edits.

## Review Focus

1. **Camera dies while the actor is half-way to confirmation.** The fallback clip must start with a fresh detector, so pending "down" evidence never carries over. Pinned in Task 6 (`test_pre_check_loss_uses_fresh_pipeline`).
2. **Fallback clip missing or unreadable.** The worker must set `offline`, the thread must exit cleanly, and the app must keep serving existing checks. Pinned in Task 6 (`test_missing_fallback_goes_offline`).
3. **YOLO weights missing or failing to load at startup.** The feed must show `detector_unavailable` and the staff flow must still work on samples. Pinned in Task 6 (`test_model_load_failure_marks_unavailable`).
4. **SQLite busy while an event is delivered.** Retrying must reuse the same `episode_id` and produce exactly one check. Pinned in Task 6 (`test_delivery_retry_creates_one_check`).
5. **A camera's `zone_id` missing from the map zones.** The check would show in the queue with no pin, which is the G2 bug. `load_venue` must reject it. Pinned in Task 5 (`test_unknown_camera_zone_rejected`).

## File map

| File | Responsibility | Task |
| --- | --- | --- |
| `local/cleanup.sh` | One-shot repo reorganization | 2 |
| `requirements.txt`, `requirements-posec3d.txt`, `README.md` | Environment and quick start | 4 |
| `config/venue.json`, `src/codejam16/venue.py`, `tests/test_venue.py` | Zones, map pins, camera thresholds | 5 |
| `src/codejam16/live.py`, `tests/test_live.py` | `FeedState` and `DetectorWorker` (failure policy, retry, fps) | 6 |
| `src/codejam16/web/app.py`, `tests/test_web.py` | Feed and zones in snapshot, launch flags, worker start | 7, 10 |
| `src/codejam16/store.py`, `tests/test_store.py` | Demo seed, store guarantees | 9, 11 |
| `src/codejam16/web/static/staff.js`, `templates/app.html`, `static/staff.css` | Mockup states, config-driven pins, latency/fps | 11 |
| `src/codejam16/vision_cli.py`, `src/codejam16/pipeline.py`, `tests/test_detector.py` | Pose trace kept, venue thresholds, already-down test | 3, 12 |
| `docs/detector-results.md`, `docs/rehearsal-log.md` | Evidence | 13–16 |

## Lanes and timeline

| Hours | D (detector) | S (staff app, Nicolas) | C (capture/integration) |
| --- | --- | --- | --- |
| H0–1 | Prep the clip list and mount, Task 13 step 1 | Read the mockup diff | Tasks 1–3 |
| H1–2 | Task 13 (record) | Task 9 | Task 4 |
| H2–5 | Task 13 (calibrate) | Tasks 10, 11 | Tasks 5, 6 |
| H5–7 | Task 12, Task 14 | Task 11 | Tasks 7, 8 |
| **H7** | **Checkpoint:** Tasks 1–12 merged, replay end-to-end passes Task 8 | | |
| H7–10 | Retune if needed | Task 15 | Task 15, optional Task 17 |
| H10–12 | Task 16 rehearsal 1, fixes | | |
| **H12** | **Code freeze** | | |
| H12–18 | Sleep in shifts. Task 16 rehearsals 2–3 at ~H15–16. Venue pass at ~H17. | | |

If behind at H7, drop in this order: screenshots (Task 11 step 6), the PoseC3D README section, then the optional rearm demo step. Never drop Tasks 9, 10, 13 or the fallback path.

---

## Phase R: Reorganize (C, H0–1)

### Task 1: Rescue the uncommitted pipeline work

**Files:** `~/Desktop/codejam16-live` (worktree on `team/live-person-down`, mid-merge of `team/detector` into `team/integration`)

- [ ] **Step 1: Confirm the state.** Run `git -C ~/Desktop/codejam16-live status`. Expected: "All conflicts fixed but you are still merging". The changes are `detector.py`, `vision_cli.py`, `test_detector.py` and `README.md`, plus untracked `src/codejam16/pipeline.py` and `tests/test_pipeline.py`.
- [ ] **Step 2: Run its tests.** Run `cd ~/Desktop/codejam16-live && PYTHONPATH=src ../codejam16/.venv/bin/python -m unittest discover -s tests`, using any 3.12 venv with opencv and numpy. Expected: OK.
- [ ] **Step 3: Commit and push.**

```bash
cd ~/Desktop/codejam16-live
git restore .DS_Store
git add README.md src/codejam16 tests
git commit -m "Merge detector and add capture-backed person-down pipeline"
git push origin HEAD:feat/live-pipeline
```

Expected: `origin/feat/live-pipeline` exists. **Do not run `merge --abort` anywhere before this push succeeds.**

### Task 2: Run the cleanup script

**Files:** Modify `local/cleanup.sh`

- [ ] **Step 1: Edit the script.**
  - Replace the line `git -C "$LIVE" merge --abort 2>/dev/null || true` with a guard that exits with an error if `git -C "$LIVE" rev-parse -q --verify MERGE_HEAD` succeeds. That would mean Task 1 was skipped.
  - In step 6, add `.DS_Store` to `.gitignore` and stage it.
  - Make sure `docs/superpowers/` is included by the `docs/` stash and add.
- [ ] **Step 2: Mark the old map as superseded.** Add a line at the top of `docs/BUILD-MAP.md`: "Sections 3–4 are superseded by docs/superpowers/plans/2026-11-21-codejam16-finish-and-demo.md. Sections 2, 5 and 7 remain the reference." (Section 7's R5 is decided in the spec.)
- [ ] **Step 3: Warn the team in chat** that `main` is about to fast-forward to `team/integration`.
- [ ] **Step 4: Run it.** `bash ~/Desktop/codejam16/local/cleanup.sh`. Expected: it ends with branches `main`, `chore/cleanup`, `team/detector-posec3d` and `team/staff-app`, plus a backup folder `~/Desktop/codejam16-backup-*`. `git worktree list` shows one folder.
- [ ] **Step 5: Open the PR.** `gh pr create --base main --head chore/cleanup --fill`, then squash-merge after one review.

### Task 3: Land existing branches on `main`

**Interfaces:**
- Produces: `main` containing `capture.py`, `detector.py`, `pipeline.py`, `pose_trace.py`, `posec3d_cli.py`, `vision_cli.py` (pipeline-based, with `--pose-trace`), `store.py` and `web/`.
- Produces: `PersonDownPipeline(predict_pose, *, camera_id, zone_id, floor_box, confirmation_seconds=2.0, rearm_seconds=2.0, maximum_observation_gap_ms=500, minimum_keypoint_confidence=0.4, down_angle_degrees=30, upright_angle_degrees=60, observe_pose=None)`. `observe_pose` is new and is passed through to `PersonDownDetector`.

One PR per branch, in this order. Each PR body states the test command output.

- [ ] **Step 1: Land `team/detector-posec3d`.** Branch `merge/detector` off `main` and `git merge origin/team/detector-posec3d`. README conflict: keep both sections and title the PoseC3D section "PoseC3D experiment (not in demo)". Run the tests and expect OK. Open a PR and merge it.
- [ ] **Step 2: Land `feat/live-pipeline`.** Branch `merge/pipeline` off `main` and `git merge origin/feat/live-pipeline`. In the `vision_cli.py` conflict, take the pipeline version. In the `README.md` conflict, keep all sections.
- [ ] **Step 3: Write the failing test** in `tests/test_pipeline.py`:

```python
def test_observe_pose_receives_every_frame(self):
    seen = []
    worker = PersonDownPipeline(lambda frame: frame.results, camera_id="demo-camera",
                                zone_id="east-bar", floor_box=FLOOR_BOX,
                                observe_pose=lambda ts, shape, results: seen.append(ts))
    with redirect_stderr(StringIO()):
        worker.run(samples(DOWN, 0, 1500), source="replay", deliver=lambda e: None)
    self.assertEqual(seen, [0, 500, 1000])
```

Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_pipeline -v`. Expected: FAIL with an unexpected keyword `observe_pose`.
- [ ] **Step 4: Implement.** Add `observe_pose=None` to `PersonDownPipeline.__init__` and pass it to every `PersonDownDetector(...)` it constructs. Restore the `--pose-trace PATH` flag in `vision_cli.py` from the detector-posec3d version: open the file with mode `"x"`, wrap it in `PoseTrace`, and pass it as `observe_pose`.
- [ ] **Step 5: Run all tests.** Expected: OK with 49 or more tests. Commit `"Land live pipeline and keep pose trace"`, open a PR and merge it.
- [ ] **Step 6: Land `team/staff-app`.** Branch `merge/staff-app`, merge it, and keep all README sections. Run the tests and expect OK. Open a PR and merge it.
- [ ] **Step 7: Delete merged remote branches.**

```bash
git push origin --delete team/integration team/detector team/detector-posec3d team/staff-app feat/live-pipeline
```

**Gate H1:** a fresh `git clone` of `main` passes the test command.

---

## Phase 0: Environment (C)

### Task 4: One environment and quick start

**Files:** Modify `requirements.txt`, `README.md`. Create `requirements-posec3d.txt`.

- [ ] **Step 1: Write the requirements files.** `requirements.txt` contains exactly the five Global Constraints pins. `requirements-posec3d.txt` contains the PoseC3D pin list from the current README.
- [ ] **Step 2: Rewrite the top of README** as "Quick start" with three blocks: setup, test and launch.
  - Setup: `uv venv --python 3.12 .venv`, then `uv pip install --python .venv/bin/python -r requirements.txt`, then the `curl` command for `local/yolo26n-pose.pt`, then `cp ~/Downloads/laydown.MOV local/laydown.MOV`.
  - Test: the Global Constraints test command.
  - Launch: the Task 7 commands.
  - Move the detector, capture and PoseC3D sections below it unchanged.
- [ ] **Step 3: Verify from a fresh clone.** In a temp dir: clone, run setup, run tests. Expected: OK.
- [ ] **Step 4: Commit** `"Pin one 3.12 environment and add quick start"`, open a PR and merge it.

---

## Phase 1: Pipeline into the app (C, with D for detector edges)

### Task 5: Venue config

**Files:** Create `config/venue.json`, `src/codejam16/venue.py`, `tests/test_venue.py`. Modify `fixtures/person-down.json` (`zone_id` becomes `"east-bar"`).

**Interfaces:**
- Produces: `load_venue(path: str | Path) -> Venue`
- Produces: `Venue.zones: dict[str, Zone]` and `Venue.cameras: dict[str, Camera]`
- Produces: `Zone(zone_id, name, landmark, map_x, map_y)` and `Camera(camera_id, zone_id, floor_box: tuple[float, float, float, float], detector: dict)`. `detector` holds the `PersonDownDetector` keyword thresholds.
- Produces: `Venue.zone_for_camera(camera_id) -> Zone`
- Produces: `Venue.to_public() -> list[dict]`, the zones for the browser (id, name, landmark, map_x, map_y).

`config/venue.json`:

```json
{
  "zones": {
    "east-bar": {"name": "East bar", "landmark": "Beside the side stairs", "map_x": 241, "map_y": 118},
    "main-floor": {"name": "Main floor", "landmark": "Near the north tables", "map_x": 592, "map_y": 274}
  },
  "cameras": {
    "demo-camera": {
      "zone_id": "east-bar",
      "floor_box": [0.10, 0.62, 0.95, 0.98],
      "detector": {"confirmation_seconds": 2.0, "rearm_seconds": 2.0,
                   "down_angle_degrees": 30, "upright_angle_degrees": 60,
                   "minimum_keypoint_confidence": 0.4, "maximum_observation_gap_ms": 500}
    }
  }
}
```

- [ ] **Step 1: Write failing tests:**
  - `test_loads_repo_config`: `load_venue("config/venue.json").zone_for_camera("demo-camera").name == "East bar"`.
  - `test_unknown_camera_zone_rejected`: a temp config whose camera `zone_id` is `"nowhere"` raises `ValueError` with "nowhere" in the message.
  - `test_bad_floor_box_rejected`: `[0.9, 0.6, 0.1, 0.98]` raises `ValueError`.
  - `test_to_public_lists_both_zones`: the ids are `{"east-bar", "main-floor"}`.
- [ ] **Step 2: Run `python -m unittest tests.test_venue -v`.** Expected: FAIL (no module).
- [ ] **Step 3: Implement `venue.py`** with frozen dataclasses and `json.load`. Validate that the camera zone exists and that the floor box satisfies `0 <= left < right <= 1` and `0 <= top < bottom <= 1`.
- [ ] **Step 4: Run all tests.** Expected: OK. `test_pipeline`'s fixture-keys check still passes because only the value changed.
- [ ] **Step 5: Commit** `"Add venue config as single source of zones and thresholds"`, open a PR and merge it.

### Task 6: Detector worker and feed state

**Files:** Create `src/codejam16/live.py`, `tests/test_live.py`.

**Interfaces:**
- Consumes: `PersonDownPipeline` (Task 3), `capture.open_camera(index, *, stall_timeout_seconds)`, `capture.open_replay(path, *, constant_fps=None)`, `CaptureError`, `DeliveryError`, `Venue` and `Camera` (Task 5), and `IncidentStore.create_incident(event) -> dict`.
- Produces: `FeedState()` with `.update(**fields) -> None` and `.snapshot() -> dict`. It is lock-protected and the snapshot always contains `status`, `label`, `detail`, `source`, `fps` (float) and `last_frame_at` (ISO or None).
- Produces: `DetectorWorker(*, venue: Venue, camera_id: str, deliver: Callable[[dict], dict], feed: FeedState, load_predictor: Callable[[], Callable], camera_index: int | None = None, replay_path: Path | None = None, fallback_clip: Path | None = None, open_camera=capture.open_camera, open_replay=capture.open_replay, delivery_attempts: int = 5)`
- Produces: `.run() -> None` (blocking) and `.start() -> threading.Thread` (a daemon thread named `detector-worker`).
- Behaviour:
  - Before delivery, the worker enriches each event with `zone_name` and `landmark` from the venue.
  - It counts frames through a wrapping iterator and updates `fps` and `last_frame_at` at most once per second.

**Status and label table (exact):**

| Situation | status | label |
| --- | --- | --- |
| Model load raised | `detector_unavailable` | `Detector unavailable` |
| Camera frames flowing | `live` | `Live camera` |
| Replay flowing (`--replay`, or fallback) | `replay` | `Rehearsal clip` |
| Camera lost after a check was delivered, or fallback failed | `offline` | `Camera offline` |
| Replay ended normally | `finished` | `Rehearsal clip ended` |

- [ ] **Step 1: Write failing tests** with a fake predictor (`lambda frame: frame.results`) and fake openers. A fake opener is a context manager yielding `CapturedFrame` lists built with `test_detector`'s `DOWN`, `UPRIGHT` and `SITTING`. For live loss, it raises `LiveFeedLost` after the list.
  - `test_replay_creates_one_enriched_check`: replay of DOWN 0–3000 ms. `deliver` is called once with `zone_id == "east-bar"` and `zone_name == "East bar"`, and the final feed status is `finished`.
  - `test_pre_check_loss_uses_fresh_pipeline`: live DOWN 0–1500 ms then loss, with a fallback of DOWN 0–1000 ms. Expect **zero** deliveries (the 1.5 s of evidence did not carry over) and a final status of `finished`.
  - `test_pre_check_loss_fallback_detects`: live SITTING then loss, with a fallback of DOWN 0–3000 ms. Expect one delivery whose event has `source == "replay"`.
  - `test_post_check_loss_goes_offline`: live DOWN 0–3000 ms then loss, with a fallback configured. Expect one delivery, the fallback opener **never called**, and status `offline`.
  - `test_missing_fallback_goes_offline`: live loss, and the fallback opener raises `CaptureOpenError`. Expect status `offline` and `run()` to return without raising.
  - `test_model_load_failure_marks_unavailable`: `load_predictor` raises `RuntimeError`. Expect status `detector_unavailable`, no opener called, and `run()` to return.
  - `test_delivery_retry_creates_one_check`: `deliver` wraps a real `IncidentStore` on a temp file and raises `sqlite3.OperationalError("database is locked")` on its first call. Expect `store.count() == 1` and the stored id to equal the event's `episode_id`.
  - `test_fps_reported`: after a replay of 10 frames, `feed.snapshot()["fps"] > 0` and `last_frame_at` ends with `Z`.
- [ ] **Step 2: Run `python -m unittest tests.test_live -v`.** Expected: FAIL (no module).
- [ ] **Step 3: Implement `live.py`.**
  - The worker opens sources itself through the injected `open_camera`/`open_replay` and calls `pipeline.run(frames, source=..., deliver=...)`. It does **not** use `pipeline.run_camera`/`run_replay`, which hardcode the capture module.
  - Live capture already keeps only the newest frame (`_LiveCamera._pending`), so BUILD-MAP 1.3 needs no work.
  - `run()` loads the predictor once. It builds a `PersonDownPipeline` from `venue.cameras[camera_id]` (floor_box plus the `detector` dict).
  - It runs live if `camera_index` is set, otherwise replay.
  - On `CaptureError` from live: if `pipeline.event_emitted` is true, go `offline`. Otherwise build a **new** pipeline and run the fallback.
  - On `DeliveryError`: `pipeline.run` has stopped mid-iteration. Retry `pipeline.deliver_pending(deliver)` up to `delivery_attempts` times with a 0.2 s sleep, then call `pipeline.run` again with the **same** frames iterator to resume. If every attempt fails, set status `offline` with detail `Could not save check` and return. Replace the sleep with a no-op in tests through a `sleep` keyword argument.
  - Every exception path ends in a feed status from the table. The thread never dies silently.
- [ ] **Step 4: Run all tests.** Expected: OK.
- [ ] **Step 5: Commit** `"Add detector worker with feed state and capture failure policy"`, open a PR and merge it.

### Task 7: Wire the worker into the app

**Files:** Modify `src/codejam16/web/app.py`. Test in `tests/test_web.py` (create the file here; Task 10 extends it).

**Interfaces:**
- Consumes: `FeedState`, `DetectorWorker` (Task 6), `load_venue` (Task 5).
- Produces: `create_app(database_path=None, *, seed_demo=True, venue=None, feed=None) -> Flask`.
  - `venue` defaults to `load_venue(<repo>/config/venue.json)`.
  - `feed` defaults to a `FeedState` with status `rehearsal`, label `Rehearsal mode`.
- Produces: `/api/state` includes `"feed": feed.snapshot()` and `"zones": venue.to_public()`.
- Produces: launch flags in `main()`: `--camera-index N | --replay PATH | --no-detector` (mutually exclusive, default `--no-detector`), `--fallback-clip PATH`, `--venue PATH`, `--camera-id demo-camera`, `--weights local/yolo26n-pose.pt`.
- `main()` builds the app, then (unless `--no-detector`) starts exactly one `DetectorWorker` with `deliver=store.create_incident` and `load_predictor=functools.partial(vision_cli._load_pose_predictor, weights)`. It then runs `app.run(..., use_reloader=False)`.
- `create_app` itself never starts a worker, so tests and module import stay camera-free.

- [ ] **Step 1: Write failing tests** with a client logged in through `/join/responder-ada` (POST):
  - `test_state_includes_feed_and_zones`: `create_app(tmp_db, feed=f)` with `f.update(status="live", label="Live camera", fps=12.5)`. `/api/state` JSON has `feed.status == "live"`, `feed.fps == 12.5`, and zone ids `{"east-bar", "main-floor"}`.
  - `test_create_app_starts_no_worker`: no thread named `detector-worker` is alive after `create_app`.
- [ ] **Step 2: Run `python -m unittest tests.test_web -v`.** Expected: FAIL.
- [ ] **Step 3: Implement** the signature, snapshot fields and `main()` flags above. Replace both `store.snapshot(..., app.extensions["feed_state"])` calls with `feed.snapshot()`.
- [ ] **Step 4: Run all tests.** Expected: OK.
- [ ] **Step 5: Commit** `"Run detector worker inside the staff app"`, open a PR and merge it.

### Task 8: End-to-end replay gate (C, manual)

- [ ] **Step 1: Launch.** Run `PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false .venv/bin/python -m codejam16.web.app --replay local/laydown.MOV --host 127.0.0.1`.
- [ ] **Step 2: Open the views.** Open `/join/supervisor-morgan` and `/join/responder-ada` in two Chrome profiles.
- [ ] **Step 3: Check the result.** Expected:
  - Exactly one new "Possible person down · East bar" check appears.
  - The East bar pin appears within 2 s of the stderr `Confirmed …` line.
  - The pill reads Rehearsal clip, then Rehearsal clip ended.
  - The fps shows about 12 or higher.
- [ ] **Step 4: Run a live check.** Relaunch with `--camera-index N --fallback-clip local/laydown.MOV`. Cover or stop the camera before anyone lies down. Expected: it switches to Rehearsal clip and one check appears. Then relaunch, let a check appear live, and kill the camera. Expected: Camera offline, and the check stays actionable.
- [ ] **Step 5: Record the results.** Write the outcome and measured numbers in `docs/rehearsal-log.md` (create it with columns: date, source, fps, detection→screen ms, false alerts, what broke). Commit.

---

## Phase 2: Tests (S, parallel with Phase 1)

### Task 9: Store guarantees

**Files:** Create `tests/test_store.py`. Modify `store.py` only if a test exposes a bug.

**Interfaces:** Consumes `IncidentStore(path, overdue_seconds)`, `.create_incident`, `.transition(incident_id, actor, action, outcome=None) -> (ok, message, incident)`, `.snapshot(viewer, feed)` and `._history`.

Actors: `ADA = {"id": "responder-ada", "name": "Ada Chen", "role": "responder"}`, `JORDAN = {..."responder-jordan"...}`, `MORGAN = {..."supervisor-morgan", "role": "supervisor"}`. Each test uses a fresh temp-file DB and the fixture event with a unique `episode_id`.

- [ ] **Step 1: Write the tests.**
  - `test_concurrent_accept_has_one_winner`: 50 iterations. In each, two threads call `transition(id, ADA/JORDAN, "accept")` behind a `threading.Barrier(2)`. Exactly one `ok` is True, the loser's message is `"This check already has an owner"`, and the loser's returned incident `owner_id` equals the winner's.
  - `test_non_owner_cannot_arrive_or_resolve`: after ADA accepts, JORDAN's `arrive` fails and status stays `accepted`.
  - `test_resolve_requires_arrived_and_valid_outcome`: resolve while `accepted` fails. After arriving, outcome `"Fine"` fails.
  - `test_each_outcome_saved`: for each of the three exact outcomes, the resolved incident's `outcome` matches, `resolved_at` ends with `Z`, and the last history row has `actor_id == "responder-ada"`.
  - `test_supervisor_cannot_act`: MORGAN's `accept` fails with `"Only responders can update a check"`.
  - `test_duplicate_episode_creates_one_check`: `create_incident(event)` twice gives `count() == 1` and one `created` history row.
  - `test_history_failure_rolls_back`: patch `store._history` to raise `sqlite3.OperationalError`. `accept` raises, and afterwards `get_incident(id)["status"] == "needs_responder"` and `owner_id is None`.
  - `test_overdue_flag`: `IncidentStore(path, overdue_seconds=1)`, with an event whose `detected_at` is 5 s ago. `snapshot` marks it `overdue`. After ADA accepts, `overdue` is False.
- [ ] **Step 2: Run `python -m unittest tests.test_store -v`.** Expected: all PASS. The trial run already showed one winner in 50 races. Any failure is a real bug: fix it in `store.py` within this task.
- [ ] **Step 3: Commit** `"Add incident store guarantee tests"`, open a PR and merge it.

### Task 10: Web workflow

**Files:** Extend `tests/test_web.py` (created in Task 7. If Task 7 hasn't merged yet, create the file and rebase later).

Helper: `join(client, token)` posts `/join/<token>` and returns the CSRF token from `/api/state`. Use `create_app(tmp_db, seed_demo=False)` and create the fixture event directly through `app.extensions["incident_store"]`.

- [ ] **Step 1: Write the tests.**
  - `test_valid_join_sets_cookie_and_redirects`: POST `/join/responder-ada` returns 302 to `/`, sets the `codejam_staff` cookie with HttpOnly.
  - `test_invalid_join_404`: GET `/join/nope` returns 404.
  - `test_action_without_csrf_403` and `test_action_bad_origin_403`: the second sends `Origin: http://evil.test`.
  - `test_supervisor_action_409`.
  - `test_two_clients_race_one_owner`: two clients (Ada and Jordan) accept the same check. One gets 200 and the other 409, and the 409 body's `check.owner_name` names the winner.
  - `test_refresh_keeps_owner`: the winner's `/api/state` shows `owner_id` equal to its own viewer id, and status `accepted`.
  - `test_resolved_moves_to_recent`: accept, arrive, then resolve with `False alarm`. The check is not in `checks`, is in `recent`, and has outcome `False alarm`.
- [ ] **Step 2: Run `python -m unittest tests.test_web -v`.** Expected: PASS. Fix any failure in `app.py` within this task.
- [ ] **Step 3: Commit** `"Add staff web workflow tests"`, open a PR and merge it.

---

## Phase 3: UI alignment (S)

### Task 11: Match the locked mockup and drive pins from config

**Files:** Modify `store.py` (`seed_demo`), `web/static/staff.js`, `web/templates/app.html`, `web/static/staff.css`. Test in `tests/test_store.py`.

**Interfaces:** Consumes `snapshot.zones` (list of `{id, name, landmark, map_x, map_y}`) and `snapshot.feed.fps` (Task 7).

- [ ] **Step 1: Write the failing test** `test_seed_demo_only_main_floor_sample`: after `seed_demo()`, exactly one incident exists, with `zone_id == "main-floor"` and `source == "rehearsal"`.
- [ ] **Step 2: Run it.** Expected: FAIL (two seeded).
- [ ] **Step 3: Implement the seed change.** Remove the East bar sample from `seed_demo`. Back-date the Main floor sample to `overdue_seconds + 30` seconds ago so its overdue badge is visible at demo start.
- [ ] **Step 4: Update the UI to match `docs/mockups/staff-app.html`.**
  - `statusLabel`: `accepted` and `arrived` become `"Being resolved"`, and `resolved` becomes `"Closed"`.
  - Status dot and pin fill use the mockup's teal for accepted and arrived, coral `#d97662` for needs_responder, a darker coral ring for overdue, and the existing sage for none.
  - Delete the two hardcoded `<g class="map-pin-group">` elements and the `east`/`main` counters. Render one pin per `snapshot.zones` entry at `(map_x, map_y)`, keyed by `data-zone`.
  - Sidebar zone names and the `openQueueForZone` toast use `zones[].name`.
  - Feed card: show `feed.label` and `"<fps> fps"`. The "Camera 01 · East bar" line comes from the zone.
  - **Latency:** keep a `Map` of incident id to first-render time. On first render of a non-sample check, compute `firstRender - Date.parse(detected_at)` and show `Detection → screen: 1.3 s` in the detail panel. Also `console.info` it for the rehearsal log.
- [ ] **Step 5: Check by hand.** Run `--no-detector` and use the Task 8 setup with ADA and JORDAN. Walk needs → being resolved → closed next to the mockup. Expected: no state or colour is missing.
- [ ] **Step 6: Re-shoot screenshots.** Update `docs/screenshots/responder.png` (phone width) and `supervisor.png` (laptop). This is the first thing to drop if behind.
- [ ] **Step 7: Run all tests.** Expected: OK. Commit `"Match locked UI states and drive map pins from venue config"`, open a PR and merge it.

---

## Phase 4: Detector validation (D + a consenting actor)

### Task 12: Thresholds from config, plus the already-down test

**Files:** Modify `src/codejam16/vision_cli.py`, `tests/test_detector.py`.

**Interfaces:** Consumes `load_venue` and `Camera` (Task 5). Produces `vision_cli --venue config/venue.json --camera-id demo-camera`, which takes floor_box and thresholds from the config. Explicit `--floor-box` and threshold flags still override it.

- [ ] **Step 1: Write the failing tests.**
  - `test_already_down_at_startup_alerts_once` in `test_detector.py`: DOWN frames from 0 to 6000 ms every 500 ms produce exactly 1 event.
  - `test_vision_cli_reads_venue`: `vision_cli._parse_args(["x.mp4", "--venue", "config/venue.json"])`, resolved through the new helper `_detector_options(args) -> dict`, returns `floor_box == (0.10, 0.62, 0.95, 0.98)` and `zone_id == "east-bar"`.
- [ ] **Step 2: Run them.** Expected: the first likely PASSES (the behaviour already exists, so the test just pins it). The second FAILS.
- [ ] **Step 3: Implement `_detector_options`.** Start from the venue camera, then apply CLI overrides on top.
- [ ] **Step 4: Run all tests.** Expected: OK. Commit `"Read detector thresholds from venue config"`, open a PR and merge it.

### Task 13: Record clips and calibrate

**Files:** Create `docs/detector-results.md`. Modify `config/venue.json` (floor_box and thresholds). Clips go in `local/clips/` and are never committed.

- [ ] **Step 1: Mount the camera iPhone** at the demo-day height and angle, with the floor area and actor fully in frame and the audience framed out.
- [ ] **Step 2: Record about 10 s clips** in two takes each, A for tuning and B for checking:

| Clip | Expected events |
| --- | --- |
| `walk`, `sit-chair`, `sit-floor`, `bend-pickup`, `crouch`, `tie-shoe` | 0 |
| `lie-down-stay` (lower safely, stay 10 s) | 1 |
| `start-down` (already lying when the clip starts) | 1 |
| `down-up-down` (down 5 s, stand 5 s, down 5 s) | 2 |
| `two-people` (bystander enters before confirmation) | 0, with unknown frames reported |

- [ ] **Step 3: Calibrate the floor box.** Grab one frame with `ffmpeg -i local/clips/lie-down-stay-A.mov -frames:v 1 local/frame.png`, read off the floor rectangle as fractions of width and height, and write it into `config/venue.json`.
- [ ] **Step 4: Run every A clip.**

```bash
for f in local/clips/*-A.mov; do echo "$f"; PYTHONPATH=src .venv/bin/python -m codejam16.vision_cli "$f" --venue config/venue.json 2>local/clips/$(basename "$f").log | wc -l; done
```

Expected: the event counts match the table.
- [ ] **Step 5: If any negative fires, adjust in this order:** tighten floor_box, set `confirmation_seconds` 2 → 3, set `down_angle_degrees` 30 → 20. Re-run A after each change.
- [ ] **Step 6: Run the B takes once, without tuning.** Fill in `docs/detector-results.md`, one row per clip: Clip, Expected, Events, Delay from visibly down (s), Unknown frames. **Pass bar:** 0 on all negatives, exactly the expected count on positives.
- [ ] **Step 7: Commit** the results and config, open a PR and merge it.

### Task 14: Live camera proof

- [ ] **Step 1: Prove wireless capture.** Unplug the iPhone and turn off Personal Hotspot on the camera phone and Internet Sharing on the Mac. Run `PYTHONPATH=src .venv/bin/python -m codejam16.capture_cli --camera-index N --frames 600`. Expected: frames are changing and there's no stall.
- [ ] **Step 2: Run the full app live** with `--camera-index N --fallback-clip local/clips/lie-down-stay-B.mov`. Stage sit, bend, then lie down. Expected: exactly one East bar check.
- [ ] **Step 3: Repeat over USB** as the backup.
- [ ] **Step 4: Record the results.** Log the transport, index, fps and detection → screen time in `docs/rehearsal-log.md`. Point the fallback flag at the B take from now on.

---

## Phase 5–6: Devices, network, rehearsal (all)

### Task 15: Network and devices (C + S)

- [ ] **Step 1: Set up the private router or hotspot** (not venue Wi-Fi). Launch the app with `--host 0.0.0.0`.
- [ ] **Step 2: Test the judge phone.** It opens `http://<mac-lan-ip>:5000/join/responder-ada` and joins, claims a check, and refreshes. Expected: still Ada and still the owner.
- [ ] **Step 3: Test the laptop fallback.** Use Chrome profiles "Ada" and "Jordan" plus a supervisor window. Expected: three distinct identities.
- [ ] **Step 4: Test reconnect.** Toggle the phone's Wi-Fi. Expected: "Reconnecting", then recovery to the current state.
- [ ] **Step 5: Print QR codes** for the three join URLs. Commit a log entry.

### Task 16: Rehearsals and venue pass (everyone)

- [ ] **Step 1: H10, Rehearsal 1.** Run the full BUILD-MAP §5 script with the real phones and log it. Each bug found becomes a failing test plus a fix on a `fix/…` branch before H12.
- [ ] **Step 2: H15–16, Rehearsals 2 and 3.** Exercise the camera-dead-before-check fallback in at least one of them. Gate: 2 of 3 clean.
- [ ] **Step 3: Venue pass (about 30 min, once the room is accessible).** Mount the phone, re-draw floor_box in `config/venue.json`, and run four live checks: sit, bend, lie down, stand up. Change config only. Log it.
- [ ] **Step 4: Before the demo.** Delete `local/staff.sqlite3` for a clean state. Write the launch command on a sticky note. Put the fallback table from BUILD-MAP §5 next to the laptop.

### Task 17 (optional, only if someone is idle before H7): CI

- [ ] **Step 1: Add the workflow.** Create `.github/workflows/tests.yml` running on `pull_request`: Python 3.12, `pip install Flask opencv-python-headless numpy`, then the test command. Every test must already avoid torch and YOLO.
- [ ] **Step 2: Check it works.** Open a PR. Expected: a green check. Merge it.
