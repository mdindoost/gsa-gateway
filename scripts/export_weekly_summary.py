#!/usr/bin/env python3
"""Print (and optionally save) the weekly summary to stdout / a file.

Usage:
    python scripts/export_weekly_summary.py            # prints to stdout
    python scripts/export_weekly_summary.py --save     # saves to exports/summary_<date>.txt
    python scripts/export_weekly_summary.py --days 14  # 14-day window
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import config
from bot.services.database import Database
from bot.services.summaries import SummaryService


def main() -> None:
    parser = argparse.ArgumentParser(description="Export GSA Gateway weekly summary.")
    parser.add_argument("--save", action="store_true", help="Save to exports/ directory.")
    parser.add_argument("--days", type=int, default=7, help="Summary window in days (default: 7).")
    args = parser.parse_args()

    db = Database(config.database_path)
    db.connect()
    svc = SummaryService(db)
    summary = svc.weekly_summary(days=args.days)
    db.close()

    # Strip Discord markdown for plain-text output
    plain = summary.replace("**", "").replace("_", "").replace("*", "")
    print(plain)

    if args.save:
        exports_dir = Path("exports")
        exports_dir.mkdir(exist_ok=True)
        filename = exports_dir / f"summary_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"
        filename.write_text(plain, encoding="utf-8")
        print(f"\nSaved to: {filename}")


if __name__ == "__main__":
    main()
