import json

import pytest

from calibration import load_floor_polygon, save_calibration


def test_roundtrip_and_rescale(tmp_path):
    path = tmp_path / "calibration.json"
    poly = [(0, 960), (1080, 960), (1080, 1920), (0, 1920)]
    save_calibration(path, poly, 1080, 1920, "clips/x.mp4")
    assert json.loads(path.read_text())["floor_polygon_norm"][0] == [0.0, 0.5]
    assert load_floor_polygon(path, 1080, 1920) == [(float(x), float(y)) for x, y in poly]
    # Same camera at half resolution scales with it.
    assert load_floor_polygon(path, 540, 960)[2] == (540.0, 960.0)


def test_missing_file_returns_none(tmp_path):
    assert load_floor_polygon(tmp_path / "nope.json", 100, 100) is None


def test_rejects_fewer_than_three_points(tmp_path):
    with pytest.raises(ValueError):
        save_calibration(tmp_path / "c.json", [(0, 0), (1, 1)], 10, 10, "x")
