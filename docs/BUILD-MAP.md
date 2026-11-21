# CodeJam16 build map

> **Sections 3–4 are superseded by [the finish-and-demo plan](superpowers/plans/2026-11-21-codejam16-finish-and-demo.md).** Sections 2, 5 and 7 remain the reference. R5 in section 7 is decided in [the spec](superpowers/specs/2026-11-21-codejam16-finish-and-demo-design.md).

Written 21 Nov 2026, 16:00. Single source of truth for what is left to build, test and demo.
Baseline docs: [engineering plan](designs/live-venue-welfare-checks.md), [handoff](implementation-handoff.md), [locked UI](mockups/staff-app.html).

---

## 1. Where things stand

### Branches (origin, verified from a fresh clone)

| Branch | Head | What it contains | State |
| --- | --- | --- | --- |
| `main` | `05745f9` | Docs + `fixtures/person-down.json` only | Behind everything |
| `team/integration` | `4ac1c58` | Docs + **capture adapter** (`capture.py`, `capture_cli.py`, 175-line test) via PR #2 | Integration base. No detector, no app. |
| `team/detector` | `2f79ec2` | YOLO26n-pose `PersonDownDetector` + replay CLI + tests | Fully contained in `team/detector-posec3d` |
| `team/detector-posec3d` | `6569268` | Above + pose trace + PoseC3D nausea experiment | **Not merged.** PoseC3D is outside approved scope. |
| `team/staff-app` | `e8efc3b` (Nicolas, 14:12) | Flask + SQLite store, join links, queue, supervisor map, capture | **Not merged.** Zero store/web tests. |
| `team/capture` | deleted on origin | Merged via PR #2 | Stale local branch, delete |

### Local machine problems

- **Main checkout** (`~/Desktop/codejam16`) is on `team/detector-posec3d` with uncommitted work: two edits that lock the light mockup (design doc and handoff) and the untracked `docs/mockups/staff-app.html`. Commit these before you do anything else.
- **`~/Desktop/codejam16-staff`** worktree is at `893e8f0`. It's stale, and Nicolas has pushed `e8efc3b` since.
- **`~/Desktop/codejam16-live`** worktree (`team/live-person-down`) has a merge **in progress** (`MERGE_HEAD` present). I couldn't see inside it because it's outside the connected folder. Finish or abort it.
- **Broken ref** `refs/codex/turn-diffs/...` makes every git command print `ignoring broken ref`. Delete it.

### Trial merge result (done in a scratch clone)

`team/integration` + `team/detector-posec3d` + `team/staff-app` produced **only a README.md conflict**. **All 40 existing tests pass** on the merged tree. I also ran a two-thread Accept race 50 times against `IncidentStore`: one winner every time. CSRF and join also behave correctly. The pieces are compatible, but nothing connects them yet.

### Measured detector evidence so far

`local/rehearsal/laydown.MOV` replay: 272 frames, **1 event at 8.14 s**, 12.5 fps processing, **41 frames unknown due to `multiple_people`**. This is one positive clip. There are no negative clips (sit/bend) and no live run yet.

---

## 2. Gaps (what's missing between today and a working demo)

| # | Gap | Why it matters | Severity |
| --- | --- | --- | --- |
| G1 | **No runner connects capture → detector → store.** `vision_cli` is replay-only and uses its own `cv2.VideoCapture` rather than `capture.py`. The app hardcodes feed status as "Rehearsal mode". | No live check can appear on screens. This is the core demo. | Blocker |
| G2 | **Zone mismatch.** The detector emits `zone_id="demo-area"`. The store fills `zone_name="East bar"` but keeps `zone_id=demo-area`, and the map pins are keyed on `east-bar` / `main-floor`, so **a real detection shows in the queue with no map pin.** | Supervisor map is half the demo | Blocker |
| G3 | **No store/web tests.** The handoff's required proof (claim race, owner-only, refresh keeps assignment, rollback) isn't in the repo. | Judges/reviewers, regressions during last-minute edits | High |
| G4 | **No feed-failure handling in the app**: no pre-check fallback to the clip, no post-check "camera offline". | The design's fallback plan depends on it | High |
| G5 | **Detector unvalidated on negatives**: no sit/bend/crouch clips, no "already down at startup", floor box not calibrated to the demo camera position. | False alert on stage = worst outcome | High |
| G6 | **Live camera never run through the detector.** The Continuity Camera proof (T1) isn't recorded. | Live demo dependency | High |
| G7 | **Two Python stacks.** The detector is pinned to 3.12 + torch via `uv`. The staff app says "3.10+" with Flask only. There's no single environment that runs both. | The runner can't start | Medium |
| G8 | **UI doesn't match the locked mockup.** There's no "Being resolved" state or teal pin for Accepted/Arrived, and "Closed" wording is missing. | Locked design decision | Medium |
| G9 | **No latency/fps readout.** The design requires showing measured detection→screen time and detector fps. | Explicit success criterion | Medium |
| G10 | **Overdue demo**: default 300 s, and nothing seeds an overdue check on demand. | Success criterion; R5 says rehearsal-only | Low |
| G11 | **Phone + private Wi-Fi never rehearsed** (T2). | Judge interaction | High (hardware) |
| G12 | **`multiple_people` → unknown.** A bystander in frame before confirmation blocks detection. After confirmation the episode stays latched, which is correct. | Stage blocking rule | Low (procedure) |

Already solved, no work needed: atomic one-winner claim, owner-only transitions, outcomes, episode-ID dedupe, opaque session cookie + CSRF, stale-response sequencing in `staff.js`, stale/"Reconnecting" indicator, detector latch/rearm logic.

---

## 3. Repo cleanup (do first, ~20 min, one person)

Run from `~/Desktop/codejam16`:

```sh
# 0. kill the broken ref
rm -rf .git/refs/codex

# 1. save the uncommitted mockup decision on its own branch
git stash -u
git switch -c docs/locked-ui origin/team/integration
git stash pop            # design doc + handoff edits + docs/mockups/
git add docs/ && git commit -m "Lock light staff UI mockup"
git push -u origin docs/locked-ui     # PR into team/integration

# 2. tidy the worktrees
git -C ../codejam16-live status        # finish the merge or: git -C ../codejam16-live merge --abort
git -C ../codejam16-staff pull         # pick up Nicolas's e8efc3b

# 3. drop stale branches
git branch -D team/capture team/detector
git fetch --prune
```

**Merge order into `team/integration`** (each one a PR with the evidence the handoff asks for):

1. `docs/locked-ui`
2. `team/detector-posec3d`. It merges cleanly. Keep `posec3d_cli.py` as an isolated experiment, label it "not in demo" in the README, and keep its heavy deps out of the demo requirements.
3. `team/staff-app`. Resolve the README conflict by keeping all three sections (Detector replay, Staff app, Capture probe).
4. New `team/live` branch for Phase 1 below, then into integration.
5. `team/integration` → `main` once the Phase 3 rehearsal passes. Demo from `main`.

After this, the only long-lived branches are `main` and `team/integration`. Feature branches get deleted on merge.

---

## 4. Build map

Each task lists: **owner lane · files · done when**. D = detector person, S = staff-app person, C = capture/integration person.

### Phase 0: one environment (C, 30 min) → fixes G7

- [ ] **0.1** Standardise on Python 3.12 via `uv`. Add `requirements.txt` with `Flask>=3,<4`, `ultralytics==8.4.216`, `opencv-python==5.1.0.95`, `torch==2.15.0`, `torchvision==0.29.2`. Move the PoseC3D deps to `requirements-posec3d.txt`.
- [ ] **0.2** README "Quick start": one setup block, one test command, one launch command.
- **Done when:** a fresh clone on the demo Mac runs `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` → all green, and `yolo26n-pose.pt` is cached in `local/`.

### Phase 1: wire the pipeline (C + D, 2–3 h) → fixes G1, G2, G4, G9

- [ ] **1.1 Venue config** `config/venue.json`: `{ "cameras": { "demo-camera": { "zone_id": "east-bar", "zone_name": "East bar", "landmark": "Beside the side stairs", "floor_box": [0.10,0.62,0.95,0.98] } } }`. The store looks up zone name/landmark from `zone_id` instead of hardcoded defaults, and the detector takes `zone_id` from the config. **Done when** a replay event shows a pin at East bar.
- [ ] **1.2 Detector worker** `src/codejam16/live.py`: a thread that opens `capture.open_camera(index)` or `capture.open_replay(path)`, feeds `PersonDownDetector.update(frame, timestamp_ms)`, and on an event calls `store.create_incident(event)`. It retries the insert with the same `episode_id` on failure. Load the model once at startup. A load failure sets feed status `detector_unavailable`.
- [ ] **1.3 Bounded live buffer.** Confirm `_LiveCamera` keeps only the latest frame. If it queues, change it to keep 1 pending frame so we never infer on old video.
- [ ] **1.4 Feed state** shared dict on the app (replace the hardcoded "Rehearsal mode"): `live | replay (labeled) | offline | detector_unavailable`, plus `fps`, `last_frame_at` and `source`. `/api/state` already passes `feed` through, so render it in the topbar pill + supervisor feed card.
- [ ] **1.5 Failure policy** (from the design):
  - `LiveFeedLost`/`CaptureOpenError` **before any check from this source**: build a fresh `PersonDownDetector` (clears pending evidence), switch to `open_replay(local/rehearsal-clip.mp4)` with `source="rehearsal clip"`, feed label "Rehearsal clip".
  - **After a check exists**: set `offline` and stop. The existing check stays actionable.
- [ ] **1.6 Launch flags** on `codejam16.web.app`: `--camera-index N` | `--replay PATH` | `--no-detector`, plus `--fallback-clip PATH`. Starting the server starts exactly one worker (`use_reloader=False` is already set).
- [ ] **1.7 Latency + fps.** The worker stamps `confirmed_at` (server UTC) on the event, and the store keeps it. The supervisor page records first-render time per incident and shows "Detection → screen: 1.3 s". The feed card shows detector fps. Measure on the laptop browser (same clock as the server).
- **Done when:** `--replay local/laydown.MOV` produces exactly one East bar check that appears in the queue and on the map pin within ~2 s of the console "Confirmed", and killing the camera mid-run flips the pill to Offline.

### Phase 2: tests (S + D, 1.5–2 h, parallel with Phase 1) → fixes G3

- [ ] **2.1 `tests/test_store.py`** (temp SQLite file):
  - Two threads Accept → exactly one `True` and one "already has an owner". Loop it 50×.
  - Non-owner Arrive/Resolve → denied, state unchanged.
  - Resolve without Arrived, or with a bad outcome → denied.
  - All 3 outcomes saved with actor and time.
  - Same `episode_id` twice → one incident, one history row.
  - **Rollback:** force the history insert to fail (monkeypatch `_history` to raise) → status unchanged.
  - Overdue flag true for an unowned check past the threshold, false once accepted.
- [ ] **2.2 `tests/test_web.py`** (Flask test client):
  - Valid join → cookie + redirect. Invalid token → 404.
  - Action without CSRF → 403. Bad `Origin` → 403.
  - Supervisor action → 409.
  - Two clients (Ada, Jordan) race; the loser's response carries the winner.
  - New request with the winner's cookie still shows ownership (refresh).
  - Resolved check disappears from `checks` and appears in `recent`.
- [ ] **2.3 `tests/test_live.py`** (fake predictor + fake capture iterator, no YOLO):
  - Down sequence → one incident.
  - Down continues after the staff resolve → no second incident.
  - Upright for the rearm interval then down again → second incident.
  - Feed lost before a check → replay fallback starts with a fresh detector.
  - Feed lost after a check → `offline`, and the check is still actionable.
- [ ] **2.4** Detector tests the AGENTS.md already demands are mostly in `test_detector.py`. Add "already down at startup → one alert" if missing.
- **Done when:** one command runs everything green, and the README states it.

### Phase 3: UI alignment (S, 1–1.5 h) → fixes G8, G10

- [ ] **3.1** Accepted/Arrived → label **Being resolved**, teal pin and teal row dot. Resolved → **Closed** in history, removed from the active map. Match `docs/mockups/staff-app.html`.
- [ ] **3.2** Pin colors driven by status: coral = needs responder, darker coral ring = overdue, teal = being resolved. Pins should be keyed by `zone_id` from the venue config, not hardcoded in JS.
- [ ] **3.3** Rehearsal control: `CODEJAM_OVERDUE_SECONDS=30` for rehearsal plus the existing labeled sample in Main floor. Decide R5 (section 7).
- [ ] **3.4** Re-shoot `docs/screenshots/` on the phone width and the laptop width.
- **Done when:** side-by-side with the mockup, a teammate can't spot a missing state.

### Phase 4: detector validation (D + actors, 1.5–2 h, needs the real camera spot) → fixes G5, G6, G12

- [ ] **4.1** Mount the iPhone in the **actual demo position**. Record ~10 s clips into `local/clips/`:
  - Negatives: walk through, sit on a chair, sit on the floor, bend to pick something up, crouch, tie a shoe.
  - Positives: lower to the floor and stay down 10 s; start already lying down; stay down, get up and stand 5 s, go down again.
  - Edge: two people in frame.
  - Use different takes for tuning vs. checking.
- [ ] **4.2** Calibrate `floor_box` from one frame of this camera and write it into `config/venue.json`.
- [ ] **4.3** Run `vision_cli` on every clip. Fill this table and commit it as `docs/detector-results.md`:

  | Clip | Expected | Events | Delay from visibly down | Unknown frames |
  | --- | --- | --- | --- | --- |

  **Pass bar:** 0 events on all negatives, exactly 1 per down episode, second episode only after the rearm.
- [ ] **4.4** If negatives fire: raise `confirmation_seconds` (2 → 3), tighten `down_angle_degrees` (30 → 20) and the floor box. Re-run only the *check* takes.
- [ ] **4.5** Live: `capture_cli --camera-index N --frames 600` unplugged (Continuity wireless), then the full app with `--camera-index N`. Record transport, index, fps. Repeat over USB as the backup.
- **Done when:** the results table passes, and the live run makes one check from a staged lie-down.

### Phase 5: network + devices (C + S, 45 min, hardware) → fixes G11

- [ ] **5.1** Controlled password-protected router/hotspot (not venue guest Wi-Fi). Camera iPhone Personal Hotspot OFF, Mac Internet Sharing OFF.
- [ ] **5.2** `--host 0.0.0.0`. The judge phone opens `http://<mac-lan-ip>:5000/join/responder-ada`, joins, refreshes, still shows Ada.
- [ ] **5.3** Laptop fallback: two Chrome **profiles** for Ada and Jordan plus a supervisor window. Verify they stay distinct identities.
- [ ] **5.4** Print QR codes for the join links (judge scans).
- **Done when:** the phone claims a check and survives a refresh, and a Wi-Fi toggle shows "Reconnecting" then recovers.

### Phase 6: full rehearsal ×3 (everyone, 1 h) → the gate before `main`

Run the demo script (section 5) three times end to end. Log in `docs/rehearsal-log.md`: date, source (live/clip), fps, detection→screen ms, false alerts, what broke. **Gate:** 2 of 3 runs clean, and the fallback path exercised at least once.

### Dependency view

```text
Cleanup ─> P0 env ─┬─> P1 pipeline ──┬─> P4.5 live run ─┐
                   ├─> P2 tests ─────┤                  ├─> P6 rehearsal ─> merge to main
                   ├─> P3 UI ────────┘                  │
                   └─> P4.1-4.4 clips + calibration ────┤
                       P5 network (any time, hardware) ─┘
```

Parallel lanes once P0 lands: **D** → P4.1–4.4 then P1.2/1.3 with C. **S** → P2.1/2.2 then P3. **C** → P1.1/1.4–1.7, P2.3, P5.

---

## 5. Demo script (~3 min)

Before starting: the app is running with `--camera-index N --fallback-clip local/rehearsal-clip.mp4`. Supervisor map on the projector, judge phone joined as Ada, laptop Jordan profile open. The Main floor sample check is visible and labelled "Sample".

1. **(0:00) Frame it.** "Venue staff see a camera signal as a welfare check on a live floor map, and exactly one person owns it." Point at the feed pill: Live, N fps.
2. **(0:20) Negatives.** Actor walks, bends, sits on a chair. Nothing appears. Say the rule: low + horizontal torso, sustained 2 s, in the floor zone.
3. **(0:50) Positive.** Actor lowers safely to the floor and stays. One **Possible person down · East bar** check appears on the phone and the map, and the pin pulses. Read out detection → screen time.
4. **(1:20) Claim race.** Judge taps Accept on the phone while a teammate taps Accept as Jordan. One owner everywhere, and the pin turns teal ("Being resolved").
5. **(1:45) Refresh.** Judge refreshes the phone: still owner, next action is Arrived.
6. **(2:00) Resolve.** Arrived → resolve as **Confirmed person down**. The check leaves the map and shows in recent outcomes. The actor is still down, and no second check appears (episode latched).
7. **(2:20) Rearm (optional).** Actor stands for 2 s+, goes down again → a new check.
8. **(2:40) Close.** Show the overdue badge on the Main floor sample (if R5 = show), the rehearsal log numbers, and what's next (multi-camera, more signals).

**Stage rules:** only the actor in the camera zone until the check appears (G12), soft mat on the floor, consenting teammates only, audience framed out.

### Fallbacks (decide on stage, no debugging)

| Failure | Do this |
| --- | --- |
| Camera dead before step 3 | App auto-switches to the labelled rehearsal clip (P1.5). Say "this is the recorded rehearsal through the same detector." |
| Camera dies after the check exists | Pill shows Offline. Carry on from step 4. |
| Judge phone can't reach the Mac | Use the laptop Ada + Jordan profiles for the race. The supervisor stays on the projector. |
| False alert during negatives | Accept it, resolve as **False alarm**: "this is why a human confirms." |
| App crash | Relaunch command on a sticky note. The DB in `local/` keeps state. |

---

## 6. Out of scope for the demo (do not start)

PoseC3D/nausea detection (stays on its branch as an experiment), Gemini clip review, multi-person tracking, multiple cameras, automatic dispatch, public deployment, CI.

## 7. Open decisions for the team

1. **R5: overdue in the live demo?** The design says rehearsal-only. Recommendation: show it on the labelled Main floor sample with `CODEJAM_OVERDUE_SECONDS=60`, since it costs 10 seconds of stage time.
2. **Who owns `team/live`** (P1)? It touches capture, detector and app, so one person should drive it.
3. **What's in the `codejam16-live` worktree?** If someone already started the P1 runner there, rebase that work onto the cleaned integration branch instead of starting over.
