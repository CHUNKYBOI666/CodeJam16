import json
from types import SimpleNamespace

import pytest

from location import load_location_calibration


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "setup.json"
    data = {
        "points": {"image": [[0.25, 0.2], [0.75, 0.2], [1, 0.8], [0, 0.8]]},
        "dimensions": [1920, 1080],
        "camera_id": "kitchen",
        "source": 0,
        "map_version": "kitchen-v1",
    }
    path.write_text(json.dumps(data))
    return path


def test_projects_confirming_torso_midpoint(setup):
    calibration = load_location_calibration(setup, "kitchen", 0, (1920, 1080))
    torso = SimpleNamespace(shoulder_mid=(768, 360), hip_mid=(1152, 504))
    assert calibration.map_fields(torso, (1920, 1080)) == {
        "map_x": pytest.approx(0.5), "map_y": pytest.approx(0.5), "map_version": "kitchen-v1",
    }


def test_keeps_text_only_for_outside_or_changed_capture(setup):
    calibration = load_location_calibration(setup, "kitchen", 0, (1920, 1080))
    torso = SimpleNamespace(shoulder_mid=(100, 60), hip_mid=(200, 60))
    assert calibration.map_fields(torso, (1920, 1080)) == {}
    assert calibration.map_fields(torso, (1500, 1125)) == {}
    assert calibration.map_fields(None, (1920, 1080)) == {}


@pytest.mark.parametrize("camera, source, size", [
    ("other", 0, (1920, 1080)),
    ("kitchen", 1, (1920, 1080)),
    ("kitchen", 0, (1500, 1125)),
    ("kitchen", 0, (1280, 720)),
])
def test_rejects_mismatched_camera_source_or_dimensions(setup, camera, source, size):
    with pytest.raises(ValueError, match="does not match"):
        load_location_calibration(setup, camera, source, size)


def test_rejects_crossed_corners(setup):
    data = json.loads(setup.read_text())
    points = data["points"]["image"]
    points[2], points[3] = points[3], points[2]
    setup.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="corners"):
        load_location_calibration(setup, "kitchen", 0, (1920, 1080))


def test_rejects_map_version_that_database_cannot_store(setup):
    data = json.loads(setup.read_text())
    data["map_version"] = "k" * 81
    setup.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="map_version"):
        load_location_calibration(setup, "kitchen", 0, (1920, 1080))


def test_rejects_a_corner_that_cannot_be_converted_to_float(setup):
    data = json.loads(setup.read_text())
    data["points"]["image"][0][0] = 10**400
    setup.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid location calibration"):
        load_location_calibration(setup, "kitchen", 0, (1920, 1080))
