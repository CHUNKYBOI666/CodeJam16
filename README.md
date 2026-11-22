# CodeJam16

See the [implementation handoff](docs/implementation-handoff.md) for branch ownership, shared interfaces, and review steps.

## Layout

| Path | What | Owner |
| --- | --- | --- |
| `vision/` | Capture + person-down detector: camera/clip capture, pose model, posture rule, calibration, overlay, clip evaluation | David |
| `tests/vision/` | Capture, detector and calibration tests | David |
| `backend/`, `frontend/` | Staff web app: Flask + SQLite backend, staff screens (responder queue, supervisor map) | Teammate |

## Setup (Python 3.12)

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
```

## Pose overlay

```bash
.venv/bin/python vision/run.py --source 0                    # webcam
.venv/bin/python vision/run.py --source clips/lying_down_twice.mp4   # recording (paced at real time)
```

Calibrate the floor region once per camera placement (click corners on the first frame, Enter to save to `local/calibration.json`):

```bash
.venv/bin/python vision/run.py --source clips/lying_down_twice.mp4 --calibrate
```

Flags: `--no-show`, `--save local/out.mp4`, `--every-frame` (files: no frame dropping), `--device cpu`,
`--constant-fps 30` (files whose decoder reports no usable media timestamps), `--stall-timeout 5`.
Weights download to `local/weights/` on first run. `clips/` and `local/` are git-ignored.

Alerts are printed to stdout as one JSON object per line, with the fields of
`fixtures/person-down.json`; logs go to stderr. Set the ids with `--camera-id` and `--zone-id`:

```bash
.venv/bin/python vision/run.py --source 0 --zone-id east-bar > local/alerts.jsonl
```

## Where things live

| Folder | What it holds |
| --- | --- |
| `vision/` | Everything that sees: camera and clip capture, pose, calibration, the person-down detector, the overlay runner, clip evaluation |
| `tests/vision/` | Tests for `vision/`. They need no camera and no model weights |
| `backend/`, `frontend/` | The Flask app and staff screens. They arrive when the staff app lands on `main` |
| `docs/`, `fixtures/` | Design docs and plans; the shared incident event example |
| `local/`, `clips/` | Git-ignored: recordings, weights, calibration, databases |

## Capture probe on macOS

Checks that a camera or clip delivers changing frames, without running the pose model:

```bash
.venv/bin/python vision/capture_probe.py --source clips/lying_down_twice.mp4 --frames 30
.venv/bin/python vision/capture_probe.py --source 0 --frames 120
```

Use a staged local clip with movement. If the decoder does not expose increasing
media timestamps, re-export the clip or pass its verified constant frame rate,
for example `--constant-fps 30`. Use the override only for constant frame rate
footage. A failed decode can look like ordinary end of file when the backend
does not report the expected frame count.

For the camera probe, grant camera access if macOS prompts, or check **System
Settings → Privacy & Security → Camera** for the app that launches Python.
Follow [Apple's Continuity Camera setup](https://support.apple.com/en-us/102546).
Keep the iPhone near the Mac with Wi-Fi and Bluetooth enabled, Personal Hotspot
off, and the Mac's Internet Sharing off. Lock and mount the iPhone. Start with
it unplugged. Try camera index 0, then 1 and other available indexes as needed.
Move an object in only the phone's field of view. The probe measures pixel
changes, so sensor noise alone can also produce `changed=yes`. To identify the
iPhone index, aim the two cameras at separate areas and pause the iPhone mid-probe
to confirm which index loses its feed. Use a longer sample such as `--frames 600`
for the interruption check.

If wireless is unreliable, connect a data-capable USB cable, trust the Mac if
prompted, and repeat the probe. Record the command, Python and OpenCV versions,
camera index, transport, and result for each run. Keep clips in `clips/`.

Capture (`vision/capture.py`) reports a startup failure if no usable frame arrives
within five seconds. After startup, a failed read or a five-second gap ends the
live source (`--stall-timeout` changes the limit); `vision/run.py` then exits with code 2.
If the native read does not return, the caller still receives the timeout and
can open a replay. A stuck native reader may require restarting the process
before reopening the camera. At integration, the caller shows the camera as
offline, starts labeled replay with fresh detector timing if no check exists
yet, or lets staff finish an existing check.

## Tests and clip evaluation

```bash
.venv/bin/python -m pytest -q
.venv/bin/python vision/eval_clips.py    # every clip in clips/: alerts and down->alert time
```

## Hosted welfare checks

Open the [Vercel demo](https://codejam16-welfare-checks.vercel.app) with the room QR. Guests enter a name and receive alerts while Checks is open. One person can claim each check and only that person can mark arrival or record the outcome. The Mac publishes the event and feed status while video stays on the Mac.

### Develop and test locally

Use Node 24, Python 3.12, Docker, the Supabase CLI and `psql`.

```sh
cd backend
supabase start
supabase db reset
supabase test db
cd ..
node backend/tests/race.mjs
npm ci --prefix frontend
npm test --prefix frontend
```

Copy `frontend/.env.example` to `frontend/.env.local`. Set the local Supabase URL and the **publishable** key from the CLI's local status. Run `npm run dev --prefix frontend` and open `http://localhost:5173/?room=codejam-local`. Use two independent browser profiles to exercise simultaneous claims. To verify the packaged frontend, run `npm run build --prefix frontend`.

Set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in the Mac's shell using the local or hosted project's settings. Then run the publisher or the mock from the repo root.

```sh
python tools/mock_check.py
python tools/mock_check.py --episode-id demo-dedupe
python tools/mock_check.py --episode-id demo-dedupe
python vision/run.py --source local/rehearsal.mp4 --publish
python vision/run.py --source 0 --publish
python -m pytest -q
```

### Provision and rehearse the hosted demo

1. Apply the migrations from `backend/` with `supabase db push --remote` on the linked demo project. Enable Anonymous Sign-Ins and use private Realtime channels. Set Auth Site URL to the production Vercel origin. Set the anonymous sign-in rate limit for the expected arrival burst on the shared venue IP. The local seed is for development.
2. Set a generated room code in `app_private.app_settings` using the trusted SQL editor. Upsert the `room_code` row to rotate it. Existing joined sessions retain their assignments. Start from a fresh browser after rotation to verify that the old QR fails.
3. Set `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` and `VITE_SITE_URL` in the Vercel project and deploy from the repo root. Export the publisher secret only on the Mac. The app's **Bring someone in** section shows the printable QR and join link.
4. Run a mock, then test a staged replay and the live iPhone camera. Measure time to join on Wi-Fi and cellular, database creation to first display, and detector confirmation to first display. Verify sound after joining, simultaneous claim, refresh, rename, all three outcomes, and reconnection. A refreshed mobile browser may require another tap to enable audio.
5. If capture fails before creating a check, restart vision with the labeled staged replay. If capture drops after a check exists, continue that check in the room. If internet service fails, use the Flask build from `feat/join-by-name` on a controlled LAN and two independent browser profiles.

Record results in [the rehearsal log](docs/hosted-rehearsal.md).
