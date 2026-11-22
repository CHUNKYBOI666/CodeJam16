"""Project a confirming torso point into a manually calibrated floor area."""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from pose_math import Torso

UNIT_CORNERS = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32)


@dataclass(frozen=True)
class LocationCalibration:
    transform: np.ndarray
    dimensions: tuple[int, int]
    map_version: str
    image_corners: np.ndarray

    def map_fields(self, torso: Torso | None, frame_size: tuple[int, int]) -> dict:
        if torso is None or frame_size != self.dimensions:
            return {}
        midpoint = (np.asarray(torso.shoulder_mid) + torso.hip_mid) / (2 * np.asarray(frame_size))
        projected = self.transform @ (*midpoint, 1)
        if not np.isfinite(projected).all() or projected[2] <= 0:
            return {}
        x, y = projected[:2] / projected[2]
        tolerance = 1e-6
        if not (-tolerance <= x <= 1 + tolerance and -tolerance <= y <= 1 + tolerance):
            return {}
        return {
            "map_x": float(np.clip(x, 0, 1)),
            "map_y": float(np.clip(y, 0, 1)),
            "map_version": self.map_version,
        }


def load_location_calibration(path: str | Path, camera_id: str, source: int | str,
                              frame_size: tuple[int, int]) -> LocationCalibration:
    try:
        data = json.loads(Path(path).read_text())
        dimensions = tuple(data["dimensions"])
        corners = np.array(data["points"]["image"], dtype=np.float32)
        version = data["map_version"]
        saved_camera = data["camera_id"]
        saved_source = data["source"]
    except (KeyError, OverflowError, TypeError, ValueError) as err:
        raise ValueError("invalid location calibration JSON") from err

    if len(dimensions) != 2 or any(type(value) is not int or value <= 0 for value in dimensions):
        raise ValueError("invalid location calibration dimensions")
    if not isinstance(version, str) or not version.strip() or len(version) > 80 or "\0" in version:
        raise ValueError("location calibration needs a valid map_version of up to 80 characters")
    if saved_camera != camera_id or type(saved_source) is not type(source) or saved_source != source:
        raise ValueError("location calibration does not match the camera or source")

    if corners.shape != (4, 2) or not np.all((corners >= 0) & (corners <= 1)):
        raise ValueError("location calibration needs four normalized corners")
    edges = np.roll(corners, -1, axis=0) - corners
    next_edges = np.roll(edges, -1, axis=0)
    turns = edges[:, 0] * next_edges[:, 1] - edges[:, 1] * next_edges[:, 0]
    if not np.all(turns > 1e-4):
        raise ValueError("location calibration corners must form a clockwise quadrilateral")
    transform = cv2.getPerspectiveTransform(corners, UNIT_CORNERS)
    if not np.isfinite(transform).all() or abs(np.linalg.det(transform)) < 1e-8:
        raise ValueError("location calibration transform is singular")
    # Normalize the homogeneous sign so points on the calibrated floor have positive depth.
    if (transform @ (*corners.mean(axis=0), 1))[2] < 0:
        transform = -transform
    if dimensions != frame_size:
        raise ValueError("location calibration does not match the capture dimensions")
    return LocationCalibration(transform, dimensions, version, corners)
