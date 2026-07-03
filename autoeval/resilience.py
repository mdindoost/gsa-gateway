"""Resilience for Codex-driven auto-eval runner.

Parses Codex's "try again at ..." reset time, manages status files, and provides
an auto-resume sleep helper that bridges consecutive evaluation windows.
"""
import json, os, re, time
from datetime import datetime, timedelta


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul",
     "aug", "sep", "oct", "nov", "dec"], 1)}


def _to_24h(hh: int, ap: str) -> int:
    ap = ap.upper()
    if ap == "PM" and hh != 12:
        return hh + 12
    if ap == "AM" and hh == 12:
        return 0
    return hh


def parse_reset_seconds(reason: str):
    """Seconds until Codex's 'try again at …' reset time in *reason*, or None.

    Codex emits TWO formats (both seen live):
      A) date+time   "try again at Jun 28th, 2026 3:11 AM"  -> exact datetime
      B) time only   "try again at 10:06 PM"                -> next occurrence
    Local time (matches codex output).
    """
    reason = reason or ""
    now = datetime.now()

    # Format A: month day, year H:MM AM/PM
    m = re.search(
        r"try again at\s+([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+"
        r"(\d{4})\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])",
        reason,
    )
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            try:
                target = datetime(int(m.group(3)), mon, int(m.group(2)),
                                  _to_24h(int(m.group(4)), m.group(6)),
                                  int(m.group(5)))
                return max(0.0, (target - now).total_seconds())
            except ValueError:
                pass

    # Format B: bare clock time -> next occurrence (today if future, else tomorrow)
    m = re.search(r"try again at\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])", reason)
    if not m:
        return None
    target = now.replace(hour=_to_24h(int(m.group(1)), m.group(3)),
                         minute=int(m.group(2)), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(0.0, (target - now).total_seconds())


def write_status(path: str, state: str, **fields) -> None:
    """Atomically write status to a JSON file.

    Args:
        path: Path to the status file
        state: State string (e.g. "paused", "done")
        **fields: Additional fields to include in the JSON
    """
    payload = {"state": state, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **fields}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def read_status(path: str) -> dict:
    """Read status from a JSON file, returning {} on any error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def sleep_until_reset(reason: str, default: float = 5400, buffer: float = 300,
                      sleep_fn=time.sleep) -> float:
    """Sleep until Codex reset time, with fallback to default.

    Parses the reset time from reason; if unparseable, uses default.
    Adds buffer seconds for safety. Returns total seconds slept.

    Args:
        reason: Message containing Codex's "try again at ..." reset time
        default: Fallback sleep time in seconds (default 5400 = 90 min)
        buffer: Extra seconds to wait past the parsed reset time (default 300 = 5 min)
        sleep_fn: Injected sleep function (default time.sleep; test can pass a list.append)

    Returns:
        Total seconds slept
    """
    wait = parse_reset_seconds(reason)
    total = (wait + buffer) if wait is not None else default
    sleep_fn(total)
    return total
