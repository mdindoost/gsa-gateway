#!/usr/bin/env python3
"""Create all SQLite tables for GSA Gateway.

Run from the project root:
    python scripts/init_db.py
"""

import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import config
from bot.services.database import Database


def main() -> None:
    print(f"Initialising database at: {config.database_path}")
    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.close()
    print("Done. All tables created (or already exist).")


if __name__ == "__main__":
    main()
