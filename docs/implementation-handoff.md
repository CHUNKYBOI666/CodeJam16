# Three-person implementation handoff

Use [Repository Guidelines](../AGENTS.md) and the [engineering plan](designs/live-venue-welfare-checks.md) as the shared baseline. `team/integration` holds the baseline and receives finished work. The paths below reserve ownership for planned modules.

| Teammate | Branch | Owns | First deliverable |
| --- | --- | --- | --- |
| 1 · Detector | `team/detector` | `src/codejam16/detector.py`, `src/codejam16/vision_cli.py`, detector tests | Run local pretrained YOLO26n-pose on a staged clip. Print one timestamped event per sustained down episode. Check ordinary movement, losing sight of the actor, and recovery before a second episode. |
| 2 · Camera capture | `team/capture` | `src/codejam16/capture.py`, capture tests and device notes | Supply the same frame format for file replay and Continuity Camera. Prove changing frames from a recording and from the unplugged iPhone on the demo Mac. Document the USB fallback and how capture reports a stalled or disconnected feed. |
| 3 · Staff app | `team/staff-app` | `src/codejam16/web/`, `src/codejam16/store.py`, staff workflow tests | Seed Flask and SQLite with the sample event. Verify two responders can claim at once, one wins, and the winner keeps their assignment after refresh and can record an outcome. |

## Shared interfaces

- **Capture to detector.** Supply OpenCV BGR frames and an increasing source timestamp in milliseconds. Use elapsed capture time for live video and media time for replay. Surface end of file and live feed loss explicitly. The detector teammate can use a temporary file reader in their own branch until the capture adapter is ready.
- **Detector to staff app.** Use the fields in [person-down.json](../fixtures/person-down.json). The detector creates a stable episode ID per alert and reuses it if an insert is retried. The timestamp is UTC. The app stores the check and its initial history together and deduplicates by episode ID. The sample is synthetic, so it can seed the staff workflow before the detector runs.
- **Integration.** One teammate owns shared setup files and any interface changes. Coordinate those changes before editing them in multiple branches. Keep staged clips, downloaded weights, and demo databases in `local/`.

## Review and integration

1. Get the detector running on replay first. Camera capture and the sample-based staff flow can proceed independently against the interfaces above.
2. Each teammate reviews their diff against the assigned deliverable. Report the exact command or rehearsal steps used, what passed, and what still needs live hardware. Keep branch changes small enough to review by responsibility.
3. Integrate capture and detector, then connect real events to the staff app. Verify the full workflow with two independent responder sessions. Add map zones after the detector passes the rehearsal check.

The teammate branches are published. Open feature pull requests into `team/integration` and include the evidence from step 2 in each pull request.
