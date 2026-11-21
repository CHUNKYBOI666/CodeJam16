# Implementation handoff

Use [Repository Guidelines](../AGENTS.md) and the [engineering plan](designs/live-venue-welfare-checks.md) as the shared baseline. `team/integration` holds the baseline and receives finished work. The paths below reserve ownership for planned modules.

| Area | Branch | Owns | First deliverable |
| --- | --- | --- | --- |
| 1 · Capture + detector (David) | `team/vision` | `vision/` (capture, pose model, detector, calibration, overlay, clip evaluation, capture probe), `tests/vision/` | Run local pretrained YOLO26n-pose on staged clips and the live iPhone feed through the same capture path. Print one timestamped event per sustained down episode. Check ordinary movement, losing sight of the actor, and recovery before a second episode. Prove changing frames from the unplugged iPhone, document the USB fallback, and report a stalled or disconnected feed. |
| 2 · Staff web app (teammate) | `team/staff-app` | `src/codejam16/web/`, `src/codejam16/store.py`, staff workflow tests | Seed Flask and SQLite with the sample event. Match the locked light UI in [staff-app.html](mockups/staff-app.html). Verify two responders can claim at once, one wins, and the winner keeps their assignment after refresh and can record an outcome. |

Camera capture was originally a separate area on `team/capture`. Its adapter was folded into `vision/capture.py` and that branch is retired. The early `team/detector` attempt is superseded by `vision/detector.py`.

## Shared interfaces

- **Capture to detector.** Internal to `vision/`. `vision/capture.py` supplies OpenCV BGR frames with an increasing source timestamp in milliseconds: elapsed capture time for live video and media time for replay. End of file, failure to open, decode errors, and live feed loss are reported explicitly.
- **Detector to staff app.** Use the fields in [person-down.json](../fixtures/person-down.json). The detector creates a stable episode ID per alert and reuses it if an insert is retried. The timestamp is UTC. The app stores the check and its initial history together and deduplicates by episode ID. The sample is synthetic, so it can seed the staff workflow before the detector runs.
- **Integration.** One teammate owns shared setup files and any interface changes. Coordinate those changes before editing them in multiple branches. Keep staged clips in `clips/` and downloaded weights and demo databases in `local/`. Both are git-ignored.

## Review and integration

1. Get the detector running on replay first. The sample-based staff flow can proceed independently against the interfaces above.
2. Each teammate reviews their diff against the assigned deliverable. Report the exact command or rehearsal steps used, what passed, and what still needs live hardware. Keep branch changes small enough to review by responsibility.
3. Connect real detector events to the staff app. Verify the full workflow with two independent responder sessions. Add map zones after the detector passes the rehearsal check.

The teammate branches are published. Open feature pull requests into `team/integration` and include the evidence from step 2 in each pull request.
