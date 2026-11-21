# CodeJam16

See the [three-person implementation handoff](docs/implementation-handoff.md) for branch ownership, shared interfaces, and review steps.

## Capture probe on macOS

Use Python 3.10 or newer with a compatible OpenCV wheel. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install opencv-python
PYTHONPATH=src python -m unittest discover -s tests -p test_capture.py
PYTHONPATH=src python -m codejam16.capture_cli --file local/rehearsal.mp4 --frames 30
PYTHONPATH=src python -m codejam16.capture_cli --camera-index 0 --frames 120
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
camera index, transport, and result for each run. Keep clips in `local/`.

Capture reports a startup failure if no usable frame arrives within five
seconds. After startup, a failed read or a five-second gap ends the live source.
If the native read does not return, the caller still receives the timeout and
can open a replay. A stuck native reader may require restarting the process
before reopening the camera. At integration, the caller shows the camera as
offline, starts labeled replay with fresh detector timing if no check exists
yet, or lets staff finish an existing check.

## Setup (Python 3.12)

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
```

## Pose overlay

```bash
.venv/bin/python cv/run.py --source 0                    # webcam
.venv/bin/python cv/run.py --source clips/lie_down.mp4   # recording (paced at real time)
```

Flags: `--no-show`, `--save local/out.mp4`, `--every-frame` (files: no frame dropping), `--device cpu`.
Weights download to `local/weights/` on first run. `clips/` and `local/` are git-ignored.
