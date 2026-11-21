# Rehearsal log

One row per run. Measure detection → screen time on the laptop browser (same clock as the server).

| Date / time | Source | Detector fps (steady) | Detection → screen | False alerts | What broke / notes |
| --- | --- | --- | --- | --- | --- |
| 2026-11-21 17:00 | Replay `local/laydown.MOV`, Mac, no camera | 12.5 (7.0 average including ~12 s model load) | ≤ 1.4 s via API poll (check committed 17:00:40.6, seen at the 17:00:42 poll) | 0 | Plan Task 8 replay gate **passed**: one "East bar · Beside the side stairs" check, `source=replay`, 41/272 frames unknown (`multiple_people`), confirmation at 8.14 s clip time. The feed pill says "Rehearsal mode" during the ~12 s model load. |
