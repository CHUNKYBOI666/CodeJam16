"""Floor-region calibration file: a polygon stored in normalized (0..1) image coordinates.

Normalized so one calibration works for the same camera at any capture resolution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

Point = tuple[float, float]


def save_calibration(path: str | Path, polygon_px: Sequence[Point], width: int, height: int, source: str) -> dict:
    if len(polygon_px) < 3:
        raise ValueError("floor polygon needs at least 3 points")
    data = {
        "floor_polygon_norm": [[round(x / width, 5), round(y / height, 5)] for x, y in polygon_px],
        "image_size": [width, height],
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def load_floor_polygon(path: str | Path, width: int, height: int) -> list[Point] | None:
    """Floor polygon in pixels for a frame of this size, or None if no calibration file."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return [(x * width, y * height) for x, y in data["floor_polygon_norm"]]
