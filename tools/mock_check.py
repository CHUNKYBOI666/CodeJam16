"""Create a labeled rehearsal check using the canonical event fixture."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vision.publish import Publisher, PublishError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone-id")
    parser.add_argument("--episode-id", help="Reuse an ID to test idempotency")
    args = parser.parse_args()
    event = json.loads((ROOT / "fixtures/person-down.json").read_text())
    event.update(
        episode_id=args.episode_id or f"mock-{uuid4()}",
        source="mock",
        detected_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if args.zone_id:
        event["zone_id"] = args.zone_id
    try:
        check = Publisher.from_env().publish_check(event)
        print(json.dumps(check))
    except PublishError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
