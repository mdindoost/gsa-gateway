#!/usr/bin/env python3
"""Export events to website/data/events.json for the static website.

Reads from both events.yml (static data) and the SQLite events table
(events added via /admin_add_event). Merges by name, deduplicates,
adds a status field, and sorts by date.

Run from the project root:
    python scripts/export_events_json.py

Or call export_events_to_json(db=<Database instance>) from bot code.
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_YML = Path(__file__).parent.parent / "bot" / "data" / "events.yml"
OUT_JSON  = Path(__file__).parent.parent / "website" / "data" / "events.json"


def _status(date_str: str) -> str:
    """Return 'past', 'upcoming' (next 7 days), or 'future'."""
    try:
        d = date.fromisoformat(str(date_str))
    except ValueError:
        return "future"
    today = date.today()
    if d < today:
        return "past"
    if d <= today + timedelta(days=7):
        return "upcoming"
    return "future"


def export_events_to_json(db=None) -> int:
    """Export events to website/data/events.json.

    Merges YAML events with SQLite events (db optional).
    Returns the number of events written.
    """
    events: list[dict] = []
    seen_names: set[str] = set()

    # ── Load from YAML ─────────────────────────────────────────────────────────
    if DATA_YML.exists():
        with open(DATA_YML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for ev in data.get("events", []):
            name = ev.get("name", "")
            seen_names.add(name)
            events.append({
                "name":        name,
                "date":        str(ev.get("date", "")),
                "time":        ev.get("time", "TBD"),
                "location":    ev.get("location", "TBD"),
                "description": (ev.get("description") or "").strip(),
                "organizer":   ev.get("organizer", "GSA"),
                "rsvp_link":   ev.get("rsvp_link", ""),
                "category":    ev.get("category", "general"),
                "source":      "yaml",
            })

    # ── Load from SQLite (if db provided) ──────────────────────────────────────
    if db is not None:
        try:
            for ev in db.get_all_events():
                if ev["name"] in seen_names:
                    continue  # YAML takes precedence; skip duplicate
                seen_names.add(ev["name"])
                events.append({
                    "name":        ev["name"],
                    "date":        ev["date"],
                    "time":        ev.get("time", "TBD"),
                    "location":    ev.get("location", "TBD"),
                    "description": ev.get("description", ""),
                    "organizer":   ev.get("organizer", "GSA"),
                    "rsvp_link":   ev.get("rsvp_link", ""),
                    "category":    ev.get("category", "general"),
                    "source":      "db",
                })
        except Exception as exc:
            print(f"WARNING: DB read failed: {exc}", file=sys.stderr)

    # ── Add status and sort ────────────────────────────────────────────────────
    for ev in events:
        ev["status"] = _status(ev["date"])

    today_str = date.today().isoformat()
    upcoming = sorted(
        [e for e in events if e["date"] >= today_str],
        key=lambda e: e["date"],
    )
    past = sorted(
        [e for e in events if e["date"] < today_str],
        key=lambda e: e["date"],
        reverse=True,
    )
    events = upcoming + past

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    return len(events)


def main() -> None:
    if not DATA_YML.exists():
        print(f"ERROR: {DATA_YML} not found.", file=sys.stderr)
        sys.exit(1)

    count = export_events_to_json(db=None)
    print(f"Exported {count} events → {OUT_JSON}")


if __name__ == "__main__":
    main()
