"""Scheduler — materialize due work into ``posts``, then publish.

Two materializers turn schedules into concrete ``posts`` rows:
  * recurring ``post_templates`` whose ``next_run_at`` has arrived (and advance it)
  * ``event_reminders`` whose fire time (event time minus offset) has arrived

After materializing, it asks the ``PostPublisher`` to send everything due. The
scheduler is platform-agnostic — it never touches a connector. A host process
calls ``tick()`` on an interval; everything is idempotent within a tick.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_FMT = "%Y-%m-%d %H:%M:%S"


# ── time helpers ─────────────────────────────────────────────────────────────

def _parse_hhmm(s: str, default=(9, 0)) -> tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return default


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_occurrence(recurrence: dict, after: datetime) -> datetime | None:
    """Next fire time strictly after ``after`` for a recurrence config, or None
    for non-repeating ('once'/'event_driven') schedules or past their end date."""
    freq = recurrence.get("freq", "daily")
    interval = int(recurrence.get("interval", 1))
    hh, mm = _parse_hhmm(recurrence.get("time", "09:00"))

    if freq in ("once", "event_driven", "none"):
        return None

    if freq == "daily":
        nxt = (after + timedelta(days=interval)).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
    elif freq == "weekly":
        days = sorted({int(d) for d in recurrence.get("days_of_week", [])}) or [after.weekday()]
        nxt = None
        for add in range(1, 8):
            cand = (after + timedelta(days=add)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)
            if cand.weekday() in days:
                nxt = cand
                break
        if nxt is None:  # pragma: no cover - days always non-empty
            nxt = (after + timedelta(weeks=interval)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)
    elif freq == "monthly":
        nxt = _add_months(after, interval).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
    else:
        return None

    end = recurrence.get("end")
    if end and nxt.strftime("%Y-%m-%d") > end:
        return None
    return nxt


def parse_event_datetime(date_str: str, time_str: str | None) -> datetime | None:
    """Parse event date (+ best-effort time) into a datetime. Defaults to 09:00
    when the time is 'TBD' or unparseable."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    h, m = 9, 0
    if time_str and time_str.strip().upper() != "TBD":
        mt = re.search(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", time_str)
        if mt:
            h, m = int(mt.group(1)), int(mt.group(2))
            ap = (mt.group(3) or "").upper()
        else:
            mt = re.search(r"(\d{1,2})\s*([AaPp][Mm])", time_str)
            ap = mt.group(2).upper() if mt else ""
            h = int(mt.group(1)) if mt else 9
        if ap == "PM" and h != 12:
            h += 12
        elif ap == "AM" and h == 12:
            h = 0
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


def reminder_fire_time(date_str, time_str, offset_value, offset_unit) -> datetime | None:
    dt = parse_event_datetime(date_str, time_str)
    if dt is None:
        return None
    unit = {
        "minutes": timedelta(minutes=offset_value),
        "hours": timedelta(hours=offset_value),
        "days": timedelta(days=offset_value),
        "weeks": timedelta(weeks=offset_value),
    }.get(offset_unit)
    return dt - unit if unit else None


# ── scheduler ────────────────────────────────────────────────────────────────

class Scheduler:
    def __init__(self, ops_conn, kb_conn, publisher, registry=None):
        """Two-connection constructor.

        ``ops_conn`` — OPS DB connection; used for all reads/writes of
        ``posts``, ``post_templates``, ``events``, and ``event_reminders``.

        ``kb_conn`` — Knowledge DB connection; reserved for future org/settings
        stamping (currently the publisher handles settings reads via its own
        kb_conn; this param is stored for completeness and Phase 3 use).

        For the behavior-preserving combined-file mode, pass the same connection
        for both: ``Scheduler(conn, conn, publisher)``.
        """
        self.conn = ops_conn       # OPS: all scheduling reads/writes
        self._kb_conn = kb_conn    # Knowledge: future org/settings use
        self.publisher = publisher
        # Optional deleter: unsends posts whose delete_at has passed. None in pure-materialize
        # contexts/tests; the SchedulerRunner supplies the registry so it runs in production.
        from v2.core.publishing.deleter import PostDeleter
        self.deleter = PostDeleter(ops_conn, registry) if registry is not None else None

    async def tick(self, now: datetime | None = None) -> dict:
        # UTC-canonical: scheduled_for / event datetimes are stored UTC, so "now"
        # must be UTC too. Naive UTC (tzinfo stripped) to compare with naive
        # parsed datetimes. Matches SQLite datetime('now').
        # Clear the per-tick OrgCache so the next tick sees fresh org data
        # (e.g. if an org was renamed or deactivated between ticks). MED-7/F6.
        from v2.core.publishing.org_resolve import OrgCache
        _tick_org_cache = OrgCache()
        _tick_org_cache.clear()  # explicitly cleared at top of each tick
        now_dt = now or datetime.now(timezone.utc).replace(tzinfo=None)
        templates = self.materialize_templates(now_dt)
        reminders = self.materialize_event_reminders(now_dt)
        published = await self.publisher.publish_due(now_dt.strftime(_FMT))
        # Unsend due posts AFTER publishing (disjoint rows: 'sent' vs 'scheduled'; explicit order).
        deleted = await self.deleter.delete_due(now_dt.strftime(_FMT)) if self.deleter else {}
        result = {"templates_materialized": templates,
                  "reminders_materialized": reminders, **published,
                  "deleted": deleted.get("deleted", 0),
                  "delete_unsupported": deleted.get("unsupported", 0),
                  "delete_failed": deleted.get("failed", 0)}
        logger.debug("scheduler tick: %s", result)
        return result

    def materialize_templates(self, now_dt: datetime) -> int:
        now_s = now_dt.strftime(_FMT)
        rows = self.conn.execute(
            "SELECT * FROM post_templates WHERE enabled=1 "
            "AND next_run_at IS NOT NULL AND next_run_at <= ?",
            (now_s,),
        ).fetchall()
        count = 0
        for t in rows:
            self.conn.execute(
                "INSERT INTO posts(org_id,org_slug,type,title,content,channels,discord_channel,"
                "scheduled_for,status,source_type,source_id,signature) "
                "VALUES (?,?,?,?,?,?,?,?, 'scheduled', 'template', ?, ?)",
                (t["org_id"], t["org_slug"], t["post_type"], t["name"], t["content"],
                 t["channels"], t["discord_channel"], now_s, t["id"], t["signature"]),
            )
            nxt = next_occurrence(json.loads(t["recurrence"]), now_dt)
            self.conn.execute(
                "UPDATE post_templates SET last_run_at=?, next_run_at=? WHERE id=?",
                (now_s, nxt.strftime(_FMT) if nxt else None, t["id"]),
            )
            count += 1
        self.conn.commit()
        return count

    def materialize_event_reminders(self, now_dt: datetime) -> int:
        rows = self.conn.execute(
            "SELECT er.id AS rid, er.event_id, er.offset_value, er.offset_unit, "
            "er.channels, er.template, e.name AS ev_name, e.date AS ev_date, "
            "e.time AS ev_time, e.location AS ev_loc, e.org_id AS ev_org, "
            "e.org_slug AS ev_org_slug "
            "FROM event_reminders er JOIN events e ON e.id=er.event_id "
            "WHERE er.enabled=1 AND er.post_id IS NULL"
        ).fetchall()
        count = 0
        for r in rows:
            fire = reminder_fire_time(r["ev_date"], r["ev_time"], r["offset_value"], r["offset_unit"])
            if fire is None or fire > now_dt:
                continue
            content = r["template"] or (
                f"Reminder: {r['ev_name']} is on {r['ev_date']} at {r['ev_time']}, {r['ev_loc']}."
            )
            cur = self.conn.execute(
                "INSERT INTO posts(org_id,org_slug,type,title,content,channels,scheduled_for,"
                "status,source_type,source_id) "
                "VALUES (?, ?, 'event_reminder', ?, ?, ?, ?, 'scheduled', 'event_reminder', ?)",
                (r["ev_org"], r["ev_org_slug"], f"Reminder: {r['ev_name']}", content,
                 r["channels"], now_dt.strftime(_FMT), r["event_id"]),
            )
            self.conn.execute(
                "UPDATE event_reminders SET post_id=? WHERE id=?", (cur.lastrowid, r["rid"])
            )
            count += 1
        self.conn.commit()
        return count
