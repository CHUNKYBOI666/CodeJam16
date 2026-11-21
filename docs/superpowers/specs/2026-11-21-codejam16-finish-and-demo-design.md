# Design: Finish, test and demo CodeJam16 in 18 hours

Written 21 Nov 2026. Supersedes the branch and cleanup sections of [BUILD-MAP.md](../../BUILD-MAP.md). Product behaviour is unchanged from the approved [engineering plan](../../designs/live-venue-welfare-checks.md) and the locked [staff UI](../../mockups/staff-app.html).

## 1. Intent

The repository is spread over six branches, two extra worktree folders and two docs that disagree about where work should land. The team has about 18 hours and three people. This design sets out one way to:

1. Get every finished piece of work onto one shared branch without losing anything.
2. Close the gaps between the existing pieces and the approved demo.
3. Prove it works through automated tests, a detector results table and three full rehearsals.
4. Run a 3-minute demo with rehearsed fallbacks.

**Success means** an end-to-end demo on `main` that has passed rehearsal: a live iPhone camera produces one "Possible person down · East bar" check, two responders race to claim it with one winner, the winner refreshes and resolves it, and the supervisor map follows along. Ordinary sitting and bending produce nothing.

### Goals

- One shared branch (`main`) that always runs and is always demoable.
- Every gap G1–G12 in BUILD-MAP §2 is closed or explicitly accepted.
- One command runs all tests, with no YOLO model needed.
- Measured numbers to show judges: detector fps and detection-to-screen latency.

### Non-goals

PoseC3D/nausea in the demo, Gemini review, multi-person tracking, multiple cameras, automatic dispatch, public hosting, recovery after the Mac process dies, and CI (optional, see §6).

### Anti-goals (failure even if it "works")

- A false alert on stage from sitting or bending.
- Losing the uncommitted pipeline work during cleanup.
- Ending the night with features still on side branches instead of `main`.
- Debugging on stage. Every failure has a scripted fallback.

## 2. Constraints

- **Time:** about 18 hours from the start of cleanup (H0) to the demo (H18), including sleep.
- **People:** three, in lanes **D** (detector), **S** (staff app, Nicolas) and **C** (capture and integration). Each lane owns its files (§4.3).
- **Hardware:** one arm64 Mac, the camera iPhone (Continuity Camera), a judge phone, and a private router or hotspot.
- **Runtime:** Python 3.12 via `uv`. The detector stack and Flask live in one environment.
- **Data hygiene:** clips, weights, databases and traces stay in `local/` and never go into git. Footage uses consenting teammates only.

## 3. Approaches considered

| | Approach | Trade-off | Verdict |
| --- | --- | --- | --- |
| **A** | `main` is the only shared branch, with one short-lived branch per task merged by PR within hours | `main` moves often, so people rebase often. Lane-owned files keep conflicts small. | **Chosen** |
| B | Keep `team/integration` as staging and promote it to `main` after rehearsal | Two levels of branching for 18 hours. This is the long-lived-branch drift that caused the mess. It also contradicts `local/cleanup.sh`. | Rejected |
| C | Freeze the branches and rebuild integration from scratch | Throws away 48 passing tests and serializes the team behind one person | Rejected |

Two calls within A:

- **Build the runner on the existing `pipeline.py`** in the `codejam16-live` worktree. It already wraps capture and the detector, handles feed loss, holds a pending event for retry, has `--fallback-clip`, and has 8 passing tests.
- **Keep the `--pose-trace` option** when resolving the `vision_cli.py` clash. It is small, and it is how the results table gets its evidence.

## 4. Verified current state (H0)

Verified by fetching origin and trial-merging every branch in a scratch clone with Python 3.12, Flask and OpenCV.

| Where | Contains | Notes |
| --- | --- | --- |
| `origin/main` `05745f9` | Docs and fixture | Behind everything |
| `origin/team/integration` `4ac1c58` | Capture adapter, merged via PR #2 | Fast-forwards `main` cleanly |
| `origin/team/detector-posec3d` `6569268` | Detector, replay CLI, pose trace, PoseC3D experiment | Contains `team/detector` |
| `origin/team/staff-app` `e8efc3b` | Flask app, SQLite store, join links, queue, supervisor map | No store or web tests |
| `~/Desktop/codejam16-live` (`team/live-person-down`) | **Uncommitted:** a finished merge of detector into integration, plus untracked `pipeline.py` and `test_pipeline.py`, plus a `vision_cli.py` rewritten to use the pipeline | **Exists only on this disk.** Its `vision_cli.py` drops `--pose-trace`. |
| `~/Desktop/codejam16-staff` | Stale `893e8f0` | Safe to remove |
| Main checkout | On `team/detector-posec3d`, with the locked-UI doc edits and untracked `docs/BUILD-MAP.md` and `docs/mockups/` | `local/cleanup.sh` is untested |

**Trial merge result:** integration, then detector-posec3d, then staff-app, then the live worktree files gives README.md conflicts only. **48/48 tests pass.**

**Confirmed gaps on the merged tree:**

- The detector emits `zone_id="demo-area"`. The store and map key on `east-bar`/`main-floor`, and `staff.js` hardcodes both zones.
- `app.py` hardcodes feed state as `"Rehearsal mode"`, and nothing starts a detector worker.
- `requirements.txt` lists only Flask.
- The UI has no "Being resolved" state and no teal pin.

## 5. The design

### 5.1 Reorganization (H0–H1, lane C, one person, everyone else waits ~30 min)

Order matters. The first step protects work that exists on only one disk.

1. **Rescue the live worktree.** In `codejam16-live`: add the untracked pipeline files, commit the in-progress merge plus the pipeline work as one commit, then push as `feat/live-pipeline`. Do not run `merge --abort` before this; the current `cleanup.sh` does, and would discard it.
2. **Run `local/cleanup.sh`**, updated so step 1 happens first and so it skips the abort once the merge is committed. It removes the broken `refs/codex`, backs up and removes both worktrees, fast-forwards `main` to `team/integration`, commits the docs on `chore/cleanup`, untracks `.DS_Store`, and deletes stale local branches. Tell the team before it pushes to `main`.
3. **Land the existing work on `main` by PR, in this order:**
   1. `chore/cleanup`: docs, the locked UI, this spec, and `.DS_Store` in `.gitignore`.
   2. `team/detector-posec3d`: README conflict, keep all sections. Label PoseC3D "experiment, not in demo".
   3. `feat/live-pipeline`: `vision_cli.py` conflict. Keep the pipeline-based version and re-add `--pose-trace` from detector-posec3d.
   4. `team/staff-app`: README conflict only.
4. Delete the merged remote `team/*` branches. **Gate H1:** `main` contains capture, detector, pipeline and the staff app, and all 48+ tests pass from a fresh clone.

### 5.2 Team rules (for the rest of the 18 hours)

- One branch per task (`feat/…`, `test/…`, `fix/…`), off `main`, under about 300 lines, merged within about 3 hours.
- Before pushing, run `git fetch && git rebase origin/main`. Open a PR, get one teammate to review it, squash-merge, and delete the branch.
- A PR merges only if all tests pass, and it carries its own tests.
- Every rehearsal bug becomes a test.
- **Shared files have one owner (C):** `README.md`, `requirements*.txt`, `config/venue.json`, and the event format in `fixtures/person-down.json`. Announce in chat before editing them.
- One folder per person. Use `git switch` and don't create new worktrees.

### 5.3 Build work, mapped to the gaps

Task IDs match BUILD-MAP §4. The build map's task details remain the reference. This section fixes ownership, order and checkpoints.

| Phase | Lane | Closes | Content | Gate |
| --- | --- | --- | --- | --- |
| P0 env | C | G7 | One 3.12 `requirements.txt` (Flask, ultralytics, opencv, torch pins) and `requirements-posec3d.txt` split out. README Quick start with setup, test and launch commands. | Fresh clone: green tests, weights cached |
| P1 pipeline into app | C (+D for detector edges) | G1, G2, G4, G9 | `config/venue.json` mapping camera → zone_id, name, landmark, floor_box. The detector and store read zones from the config, and map pins are keyed by zone_id. A worker thread inside the Flask process runs `PersonDownPipeline` with `deliver=store.create_incident` and retries with the same episode_id. A shared feed-state object (`live`, `replay`, `offline`, `detector_unavailable`, plus fps) is shown in the pill and the feed card. Failure policy: before any check, restart with a fresh detector on the labelled clip; after a check, go offline and keep the check. Launch flags `--camera-index`, `--replay`, `--fallback-clip`, `--no-detector`. Stamp `confirmed_at` and show detection-to-screen ms. | `--replay laydown.MOV` produces one East bar check with a map pin within 2 s |
| P2 tests | S (store/web), C (live worker) | G3 | `test_store.py`: race ×50, owner-only, outcomes, dedupe, rollback, overdue. `test_web.py`: join, CSRF, Origin, supervisor 409, two-client race, refresh, resolved moves to recent. Worker tests with a fake predictor and fake frames: one incident per episode, no new incident after staff resolve, rearm, fallback before a check, offline after a check. Detector: already down at startup. | One command, all green |
| P3 UI | S | G8, G10 | "Being resolved" and teal for Accepted/Arrived. Closed in history. Pin colours by status. Pins driven by the config zones. Overdue interval via `CODEJAM_OVERDUE_SECONDS`. Re-shoot screenshots. | No missing state next to the mockup |
| P4 detector validation | D + actor | G5, G6, G12 | Mount the phone in the demo position. Record about 12 clips (negatives, positives, already-down, rearm, two people). Calibrate floor_box into the config. Fill in `docs/detector-results.md`. Tune on some takes, check on others. Live capture over wireless, then USB. | 0 events on negatives, 1 per episode; live run makes one check |
| P5 network | C + S | G11 | Private router, join links over the LAN IP, judge phone refresh, two Chrome profiles, printed QR codes | Phone claims and survives refresh |
| P6 rehearsal ×3 | all | — | Run the BUILD-MAP §5 script and log to `docs/rehearsal-log.md` | 2 of 3 clean, fallback exercised once |

### 5.4 Timeline and checkpoints

| Hours | D (detector) | S (staff app) | C (capture/integration) |
| --- | --- | --- | --- |
| H0–1 | Wait / prep clip list, mount phone | Wait / read the mockup gaps | **Reorganization (§5.1)** |
| H1–2 | P4.1 record clips | P2 store tests | P0 env |
| H2–5 | P4.2–4.4 calibrate, results table | P2 web tests, then P3 UI | P1 config + worker + feed state |
| H5–7 | Help P1 detector edges, P4.5 live run | P3 finish, pins from config | P1 failure policy, latency, worker tests |
| **H7 checkpoint** | All P0–P3 merged to `main`; replay end-to-end works | | |
| H7–10 | Retune if negatives fire | P5 network with C | P5 network, bug fixes |
| H10–12 | Rehearsal 1 together, fix, turn every bug into a test | | |
| **H12 code freeze** | After this, only fixes for rehearsal-found bugs, each with a test | | |
| H12–18 | Sleep in shifts (two awake at a time). Rehearsals 2 and 3 at about H15–16. Set up the venue at H17. | | |

**If behind at H7:** drop P3.4 screenshots, the PoseC3D README section, and the optional rearm demo step, in that order. Never drop P2 store/web tests, P4 negatives, or the fallback path.

### 5.5 Testing strategy

| Layer | What | When | Needs |
| --- | --- | --- | --- |
| Unit | Detector state machine with fake poses; store with a temp SQLite DB | Every commit | Nothing heavy |
| Integration | Flask test client with two logged-in responders; worker with fake capture and predictor | Every PR | Nothing heavy |
| Detector evidence | Real YOLO on recorded clips compared with the expected table | After any detector or threshold change | Local clips and weights |
| Live rehearsal | Full demo script on real phones, logged | H10, H15, H16 | All hardware |

Rules: the test ships in the same PR as its feature, a red suite blocks the merge, and every rehearsal bug becomes a test.

### 5.6 Demo

Use the BUILD-MAP §5 script, stage rules and fallback table unchanged. R5 is decided: the overdue badge is shown on the labelled Main floor sample with a short `CODEJAM_OVERDUE_SECONDS`, per the build map's recommendation, because it costs about 10 seconds.

## 6. Calls made after the builder check

- **Replay input.** `laydown.MOV` lives at `~/Downloads/laydown.MOV`. P1 copies it to `local/laydown.MOV`, so P1 can start at H2 without waiting for new clips. Until D records a fresh take from the demo position in P4.1, it also serves as the fallback clip (`local/rehearsal-clip.mp4`).
- **Pushing to `main`.** The private repo on a free plan has no branch protection, so the cleanup fast-forward push works. "PR only" is a team rule, not something GitHub enforces.
- **Demo seed.** `seed_demo()` on staff-app also seeds an East bar sample, which would sit next to the live East bar check. P3 changes the demo seed to only the labelled Main floor sample.
- **Calibration location (unknown venue).** P4 calibrates early (H1–5) using the demo-day camera height and angle, even if that is in the work room. Once the team can get into the demo room, a 30-minute venue pass re-draws `floor_box` and runs four live checks: sit, bend, lie down, stand back up. All detector thresholds (`floor_box`, angles, confirmation and rearm seconds, keypoint confidence) live in `config/venue.json`, so the H12 code freeze still allows config-only changes until H17.
- **Plan format.** Bite-sized, test-first tasks grouped by lane, each with its files, test and command, so either a teammate or their coding agent can run one.

## 7. Left to the builder

- Exact worker threading (a daemon thread inside the Flask process) and how the feed-state object is shared, provided the HTTP loop never blocks on inference.
- The JSON shape of `config/venue.json`, provided zone_id is the join key everywhere.
- Retry/backoff timing for delivery.
- Whether to add a 10-minute GitHub Actions unit-test workflow. It is optional and gets done only if someone is idle before H7.
- Commit-level granularity within each phase.

## 8. Deliverables

- `main` running the demo, with all `team/*` branches deleted.
- `docs/superpowers/plans/2026-11-21-codejam16-finish-and-demo.md`: the step-by-step implementation plan, which replaces BUILD-MAP §3–4.
- `docs/detector-results.md` and `docs/rehearsal-log.md` filled in with real numbers.
