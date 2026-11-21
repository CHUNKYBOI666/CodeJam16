"""Pose model loading, warmup and primary-person keypoint extraction (shared by run/eval)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

import config
from detector import Person

REPO_ROOT = Path(__file__).resolve().parent.parent


def pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(weights: str | None = None):
    from ultralytics import YOLO

    weights_dir = REPO_ROOT / config.WEIGHTS_DIR
    weights_dir.mkdir(parents=True, exist_ok=True)
    candidates = [weights] if weights else list(config.MODEL_CANDIDATES)
    last_err = None
    for name in candidates:
        path = Path(name)
        if not path.is_absolute() and path.parent == Path("."):
            path = weights_dir / name
        try:
            model = YOLO(str(path))
            print(f"[pose] loaded {path.name}")
            return model, path.name
        except Exception as err:  # weights unavailable -> try the next candidate
            print(f"[pose] could not load {name}: {err}")
            last_err = err
    raise SystemExit(f"Detector unavailable: no pose weights loaded ({last_err})")


def predict(model, image, device: str):
    return model.predict(
        image,
        device=device,
        imgsz=config.INFERENCE_IMGSZ,
        conf=config.PERSON_MIN_CONF,
        verbose=False,
    )[0]


def warm_up(model, device: str, width: int, height: int, passes: int = config.WARMUP_PASSES) -> None:
    """Run dummy inferences at the real frame size so the first live frame isn't slow."""
    dummy = np.zeros((height, width, 3), np.uint8)
    for _ in range(passes):
        predict(model, dummy, device)


def people_from_result(result) -> list[Person]:
    """Every detected person's keypoints and box score (empty list if nobody)."""
    if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
        return []
    kp = result.keypoints
    if kp.conf is None:
        return []
    xy = kp.xy.cpu().numpy()
    conf = kp.conf.cpu().numpy()
    scores = result.boxes.conf.cpu().numpy()
    return [Person(xy[i], conf[i], float(scores[i])) for i in range(len(scores))]
