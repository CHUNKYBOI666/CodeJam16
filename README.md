# CodeJam16

Venue welfare-check demo: a fixed camera spots a possible person down, and the
responder queue and supervisor floor map share one check that exactly one
responder owns. The plan is in
[docs/superpowers/plans](docs/superpowers/plans/2026-11-21-codejam16-finish-and-demo.md).

## Quick start

Setup, once, from the repository root (Python 3.12 via [uv](https://docs.astral.sh/uv/)):

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p local
curl -fL -o local/yolo26n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
cp ~/Downloads/laydown.MOV local/laydown.MOV   # staged replay clip, never committed
```

Test (no camera or model weights needed):

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Launch the staff app. Pick one detector source:

```sh
export PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false
# Live iPhone camera, falling back to the clip if the camera fails before a check:
.venv/bin/python -m codejam16.web.app --host 0.0.0.0 --camera-index 1 --fallback-clip local/laydown.MOV
# Labelled rehearsal clip through the same detector:
.venv/bin/python -m codejam16.web.app --host 0.0.0.0 --replay local/laydown.MOV
# Sample checks only, no detector:
.venv/bin/python -m codejam16.web.app --host 0.0.0.0 --no-detector
```

Zones, map pins and detector thresholds live in `config/venue.json`. Calibrate
the camera by editing that file, not code.

## Staff app

[Phone responder preview](docs/screenshots/responder.png) · [Supervisor map preview](docs/screenshots/supervisor.png)

The local staff app provides a responder queue and supervisor floor view backed by
one SQLite incident store.

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

## Capture probe on macOS

The capture probe uses the Quick start environment. From the repository root, run:

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

## Detector replay and live pipeline

Use the Quick start environment and weights. Keep recordings and generated traces
in ignored `local/`.

Run a staged replay or a live Continuity Camera. The floor box, zone and thresholds
come from `config/venue.json`; `--floor-box LEFT TOP RIGHT BOTTOM` (fractions of the
frame) and the threshold flags override it for a single run:

```sh
PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false \
  .venv/bin/python -m codejam16.vision_cli local/staged.mp4 --pose-trace local/poses.jsonl
PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false \
  .venv/bin/python -m codejam16.vision_cli --camera-index 1
```

The event stream uses stdout and diagnostics use stderr. The optional pose trace
reuses each YOLO result. Each trace file represents one session and contains
timestamps in milliseconds, image dimensions, and 17 COCO joints with confidence.
Missing or ambiguous observations are explicit. An existing trace file is
protected from overwrite.

Choose the iPhone's device index using the capture probe above. A live command
can explicitly specify `--fallback-clip local/rehearsal.MOV`; if startup or the
feed fails before an alert, the pipeline starts a fresh labeled replay. After an
alert it reports the feed offline instead. Use `--constant-fps` only when the
clip has a verified constant rate. Events are JSON lines on stdout; status,
confirmation offset and processed FPS are on stderr. Tune
`--maximum-observation-gap-ms` using measured live processing intervals, then
check that gaps still interrupt confirmation and rearm.

For integration, `PersonDownPipeline.run(..., deliver=callback)` invokes the
callback for each confirmed event. Normal callback return acknowledges delivery.
If it raises, `DeliveryError` retains `pending_event`, including its episode ID;
call `deliver_pending(callback)` to retry before processing additional frames.
The caller should make delivery idempotent by episode ID. Do not start a second
pipeline for the same camera or reinitialize it on staff resolution. The live
camera and replay errors are surfaced to the caller. Raw video and recordings
remain local.

## PoseC3D experiment (not in demo)

Use the official MMAction2 1.2.0 source and NTU60 checkpoint with an isolated
environment on Apple Silicon. The published wheel has an incomplete source
package and its standard dependency resolution requires a decord wheel unavailable
for this platform. This pose-only invocation uses OpenCV in the YOLO environment
to decode video and passes recorded coordinates to MMAction2.

```sh
uv venv --python 3.12 local/posec3d-env
uv pip install --python local/posec3d-env/bin/python \
  mmcv-lite==2.1.0 mmengine==0.10.7 numpy==1.26.4 opencv-python==4.11.0.86 \
  torch==2.15.0 torchvision==0.29.2 scipy==1.17.1 einops==0.8.2 \
  importlib-metadata==10.2.0 decord2==3.4.0
curl -fL https://codeload.github.com/open-mmlab/mmaction2/tar.gz/refs/tags/v1.2.0 \
  -o local/mmaction2-v1.2.0.tar.gz
tar -xzf local/mmaction2-v1.2.0.tar.gz -C local
curl -fL -o local/posec3d-ntu60.pth \
  https://download.openmmlab.com/mmaction/skeleton/posec3d/slowonly_r50_u48_240e_ntu60_xsub_keypoint/slowonly_r50_u48_240e_ntu60_xsub_keypoint-f3adabf1.pth
PYTHONPATH=src:local/mmaction2-1.2.0 local/posec3d-env/bin/python \
  -m codejam16.posec3d_cli local/poses.jsonl \
  --config local/mmaction2-1.2.0/configs/skeleton/posec3d/slowonly_r50_8xb16-u48-240e_ntu60-xsub-keypoint.py \
  --checkpoint local/posec3d-ntu60.pth \
  --labels local/mmaction2-1.2.0/tools/data/skeleton/label_map_ntu60.txt
```

The default is a four-second source-time window every two seconds. Each window
selects 48 poses at evenly spaced source times and uses the single-clip validation
pipeline. A gap over 500 ms or changed image dimensions restarts the window.
Windows with fewer than 36 usable samples report insufficient evidence. A sample
is usable when at least eight joints have confidence of 0.4 or higher.
These starting thresholds need calibration on separate staged takes.

Output is one JSON object per complete window, with the nausea/vomiting class
score, strongest competing class, and CPU inference time. Partial final windows
are omitted. A trace with no complete windows fails explicitly.
Model initialization messages go to stderr.

This experiment needs staged retching, coughing, bending and ordinary movement
clips to establish accuracy and delay. Its scores are available for evaluation.
Staff illness alerts and live PoseC3D processing remain later integration work.
