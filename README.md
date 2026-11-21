# CodeJam16

See the [three-person implementation handoff](docs/implementation-handoff.md) for branch ownership, shared interfaces, and review steps.

## Detector replay

From the repository root, install the verified Python 3.12 runtime and cache the
official pretrained weights. Keep recordings and generated traces in ignored
`local/`.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python ultralytics==8.4.216 opencv-python==5.1.0.95 torch==2.15.0 torchvision==0.29.2
mkdir -p local
curl -fL -o local/yolo26n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src YOLO_CONFIG_DIR="$PWD/local/ultralytics" YOLO_AUTOINSTALL=false \
  .venv/bin/python -m codejam16.vision_cli local/staged.mp4 \
  --floor-box 0.10 0.62 0.95 0.98 --pose-trace local/poses.jsonl
```

Calibrate the floor rectangle to the camera. Its coordinates are fractions of
image width and height. The event stream uses stdout and diagnostics use stderr.
The optional pose trace reuses each YOLO result. Each trace file represents one
replay session and contains timestamps in milliseconds, image dimensions, and
17 COCO joints with confidence. Missing or ambiguous observations are explicit.
An existing trace file is protected from overwrite.

## PoseC3D experiment

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
