# CodeJam16

See the [three-person implementation handoff](docs/implementation-handoff.md) for branch ownership, shared interfaces, and review steps.

## Staff app frontend

[Phone responder preview](docs/screenshots/responder.png) · [Supervisor map preview](docs/screenshots/supervisor.png)

The local staff app provides a responder queue and supervisor floor view backed by
one SQLite incident store. On first launch it seeds two clearly labeled rehearsal
checks. The camera feed currently reports **Rehearsal mode**; the detector is not
connected to the web app yet.

Use Python 3.10 or newer from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONPATH=src python -m codejam16.web.app --host 0.0.0.0 --port 5000
```

Open `http://127.0.0.1:5000/join/responder-ada` for Ada’s responder session,
`http://127.0.0.1:5000/join/responder-jordan` for Jordan’s separate responder
session, or `http://127.0.0.1:5000/join/supervisor-morgan` for the supervisor
view. For phone access on the private demo Wi-Fi, replace `127.0.0.1` with the
Mac’s LAN address. Use separate browser profiles for Ada and Jordan so their
session cookies remain independent.

Responders can accept a check, mark arrival, and resolve it as
**Confirmed person down**, **False alarm**, or **Couldn’t verify**. The supervisor
view shows the shared venue map, active checks and recent outcomes. The app polls
the shared snapshot every 1.2 seconds while open. The database is stored in
`local/`, which is ignored by Git. Set `CODEJAM_DATABASE` to use another SQLite
path and `CODEJAM_OVERDUE_SECONDS` to change the waiting threshold (default: 300
seconds).

The demo invitation tokens above are convenient local links, not production
credentials. Keep this app on the controlled rehearsal network.

Install the capture probe dependency separately when you are ready to work on
the camera adapter:

```sh
python -m pip install opencv-python
```

## Capture probe on macOS

The capture probe uses Python 3.10 or newer with a compatible OpenCV wheel.
From the repository root, install OpenCV separately as shown above, then run:

```sh
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
