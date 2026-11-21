import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from codejam16.venue import load_venue


REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "venue.json"


def write_config(directory, *, zone_id="east-bar", floor_box=(0.10, 0.62, 0.95, 0.98)):
    config = json.loads(REPO_CONFIG.read_text(encoding="utf-8"))
    config["cameras"]["demo-camera"]["zone_id"] = zone_id
    config["cameras"]["demo-camera"]["floor_box"] = list(floor_box)
    path = Path(directory) / "venue.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class VenueTests(unittest.TestCase):
    def test_loads_repo_config(self):
        venue = load_venue(REPO_CONFIG)
        zone = venue.zone_for_camera("demo-camera")
        self.assertEqual(zone.name, "East bar")
        self.assertEqual(zone.landmark, "Beside the side stairs")
        self.assertEqual(venue.cameras["demo-camera"].floor_box, (0.10, 0.62, 0.95, 0.98))
        self.assertEqual(venue.cameras["demo-camera"].detector["confirmation_seconds"], 2.0)

    def test_unknown_camera_zone_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "nowhere"):
                load_venue(write_config(directory, zone_id="nowhere"))

    def test_bad_floor_box_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                load_venue(write_config(directory, floor_box=(0.9, 0.6, 0.1, 0.98)))

    def test_to_public_lists_both_zones(self):
        zones = load_venue(REPO_CONFIG).to_public()
        self.assertEqual({zone["id"] for zone in zones}, {"east-bar", "main-floor"})
        east = next(zone for zone in zones if zone["id"] == "east-bar")
        self.assertEqual((east["map_x"], east["map_y"]), (241, 118))


if __name__ == "__main__":
    unittest.main()
