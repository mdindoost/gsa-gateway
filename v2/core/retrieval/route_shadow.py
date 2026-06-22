"""Best-effort shadow-decision sink for the Kavosh v2.1 router (Phase 1b).

Append-only jsonl of new-vs-current routing decisions while ROUTER_V21_SHADOW is on. Logging
NEVER raises — a logging failure must not break the answer path.
"""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def log_shadow(record: dict, path: str = "logs/router_v21_shadow.jsonl") -> None:
    try:
        rec = {"ts": time.time(), **record}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - shadow logging must NEVER break the answer path
        logger.debug("shadow log failed (ignored)", exc_info=True)
