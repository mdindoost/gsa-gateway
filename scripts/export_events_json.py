#!/usr/bin/env python3
"""Export events.yml → website/data/events.json for the static website.

Run from the project root:
    python scripts/export_events_json.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_YML = Path(__file__).parent.parent / "bot" / "data" / "events.yml"
OUT_JSON = Path(__file__).parent.parent / "website" / "data" / "events.json"


def main() -> None:
    if not DATA_YML.exists():
        print(f"ERROR: {DATA_YML} not found.")
        sys.exit(1)

    with open(DATA_YML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    events = []
    for ev in data.get("events", []):
        events.append(
            {
                "name": ev.get("name", ""),
                "date": str(ev.get("date", "")),
                "time": ev.get("time", "TBD"),
                "location": ev.get("location", "TBD"),
                "description": ev.get("description", "").strip(),
                "organizer": ev.get("organizer", "GSA"),
                "rsvp_link": ev.get("rsvp_link", ""),
                "category": ev.get("category", "general"),
            }
        )

    # Sort by date
    events.sort(key=lambda e: e["date"])

    output = {
        "last_updated": datetime.utcnow().isoformat(),
        "events": events,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print(f"Exported {len(events)} events → {OUT_JSON}")


if __name__ == "__main__":
    main()
