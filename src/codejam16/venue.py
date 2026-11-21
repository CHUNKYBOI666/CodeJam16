"""Load the venue's zones, map pins and per-camera detector settings."""

from dataclasses import dataclass
import json
from pathlib import Path


DEFAULT_VENUE_PATH = Path(__file__).resolve().parents[2] / "config" / "venue.json"


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    landmark: str
    map_x: float
    map_y: float


@dataclass(frozen=True)
class Camera:
    camera_id: str
    zone_id: str
    floor_box: tuple
    detector: dict


@dataclass(frozen=True)
class Venue:
    zones: dict
    cameras: dict

    def zone_for_camera(self, camera_id):
        return self.zones[self.cameras[camera_id].zone_id]

    def to_public(self):
        return [
            {"id": zone.zone_id, "name": zone.name, "landmark": zone.landmark,
             "map_x": zone.map_x, "map_y": zone.map_y}
            for zone in self.zones.values()
        ]


def load_venue(path=DEFAULT_VENUE_PATH):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = {
        zone_id: Zone(zone_id, item["name"], item["landmark"], item["map_x"], item["map_y"])
        for zone_id, item in raw["zones"].items()
    }
    cameras = {}
    for camera_id, item in raw["cameras"].items():
        if item["zone_id"] not in zones:
            raise ValueError(f"camera {camera_id} uses unknown zone {item['zone_id']}")
        left, top, right, bottom = floor_box = tuple(float(value) for value in item["floor_box"])
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError(f"camera {camera_id} floor_box must be increasing fractions in [0, 1]")
        cameras[camera_id] = Camera(camera_id, item["zone_id"], floor_box, dict(item.get("detector", {})))
    return Venue(zones, cameras)
