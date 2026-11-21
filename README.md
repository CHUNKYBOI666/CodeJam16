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

## Person-down vision pipeline

From this checkout, create the environment and cache the pretrained model:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python ultralytics==8.4.216 opencv-python==5.1.0.95 torch==2.15.0 torchvision==0.29.2
mkdir -p local
curl -fL -o local/yolo26n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Run a staged replay or a live Continuity Camera. The floor rectangle is normalized
left, top, right, bottom and must be calibrated to the camera view:

```sh
PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false \
  .venv/bin/python -m codejam16.vision_cli local/rehearsal.MOV --floor-box 0.10 0.62 0.95 0.98
PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false \
  .venv/bin/python -m codejam16.vision_cli --camera-index 1 --floor-box 0.10 0.62 0.95 0.98
```

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
