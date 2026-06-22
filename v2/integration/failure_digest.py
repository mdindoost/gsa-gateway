"""Active failure digest (accuracy backlog #3) — push "what's failing" instead of dashboard-pull.

A buffered-lane PostSource: poll() reads the failure signals (👎 with reason tags, low-confidence
questions, counts) over a recent window and enqueues ONE admin digest post per day (idempotent via a
date dedup key); the v2 SchedulerRunner delivers it to the configured admin channel. Read-only on the
analytics tables — no answer-path effect. Modeled on v2/integration/daily_fixtures.py.

Spec: docs/superpowers/specs/2026-06-22-active-failure-digest-design.md
"""
from __future__ import annotations

import datetime
import logging

from v2.core.publishing.sources import PostDraft, PostSource, platform_channels
from v2.integration.daily_fixtures import morning_utc

logger = logging.getLogger(__name__)


def collect_failures(conn, since_iso: str, top_n: int = 10) -> dict:
    """Read-only failure signals since `since_iso` (an ISO-`T` UTC boundary that matches how
    log_question writes timestamps — R1). NULL confidence is NOT low-confidence (SQLite `< 50`
    excludes NULL — R5)."""
    thumbs_down = conn.execute(
        "SELECT q.question_text, rf.detail, q.confidence "
        "FROM response_feedback rf JOIN questions q ON rf.question_id = q.id "
        "WHERE rf.rating = 'thumbs_down' AND rf.timestamp >= ? "
        "ORDER BY rf.timestamp DESC LIMIT ?", (since_iso, top_n)).fetchall()
    low_conf = conn.execute(
        "SELECT question_text, COUNT(*) AS n, ROUND(AVG(confidence), 1) AS avg_c "
        "FROM questions WHERE confidence < 50 AND timestamp >= ? "
        "GROUP BY question_text ORDER BY n DESC LIMIT ?", (since_iso, top_n)).fetchall()

    def _count(sql, params):
        return conn.execute(sql, params).fetchone()[0]

    total = _count("SELECT COUNT(*) FROM questions WHERE timestamp >= ?", (since_iso,))
    up = _count("SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up' AND timestamp >= ?", (since_iso,))
    down = _count("SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down' AND timestamp >= ?", (since_iso,))
    regen = _count("SELECT COUNT(*) FROM response_feedback WHERE rating='regenerate' AND timestamp >= ?", (since_iso,))
    return {"thumbs_down": thumbs_down, "low_conf": low_conf,
            "total": total, "up": up, "down": down, "regen": regen}


def build_digest_body(data: dict) -> str:
    """Pure formatter — the digest text."""
    lines = ["🔎 *GSA Gateway — Failure Digest*", ""]
    lines.append(f"Questions: {data['total']} · 👍 {data['up']} · 👎 {data['down']} · 🔄 {data['regen']}")
    lines.append("_(answer-rate is a vanity metric — the 👎 below are the real signal)_")
    if data["thumbs_down"]:
        lines += ["", f"👎 *Not helpful* ({len(data['thumbs_down'])}):"]
        for q, detail, conf in data["thumbs_down"]:
            tag = f" — _{detail}_" if detail else ""
            lines.append(f"  • {q}{tag}")
    if data["low_conf"]:
        lines += ["", "📉 *Low-confidence* (candidates to add to the KB):"]
        for q, n, avg_c in data["low_conf"]:
            lines.append(f"  • {q} ({n}×)")
    return "\n".join(lines)


def build_failure_digest_draft(org_id: int, conn, since_iso: str, day: datetime.date, *,
                               channels: list[str] | None = None, discord_channel: str | None = None,
                               scheduled_for: str | None = None, top_n: int = 10,
                               always: bool = False) -> PostDraft | None:
    """Build the digest draft, or None on a quiet window (no 👎 and no low-conf), unless `always`."""
    data = collect_failures(conn, since_iso, top_n)
    if not always and not data["thumbs_down"] and not data["low_conf"]:
        return None
    return PostDraft(
        org_id=org_id,
        title="Failure Digest",
        content=build_digest_body(data),
        type="digest",
        channels=channels if channels is not None else platform_channels(),
        discord_channel=discord_channel,
        scheduled_for=scheduled_for,
        source_type="failure_digest",
        dedup_key=f"failure-digest-{day.isoformat()}",   # one per day, across restarts
    )


class FailureDigestSource(PostSource):
    """Scheduled admin failure digest. Returns [] on a quiet window; failure-isolated (a throwing
    poll never breaks the scheduler tick)."""
    name = "failure_digest"

    def __init__(self, conn, org_id: int, *, channels: list[str] | None = None,
                 discord_channel: str | None = None, period_days: int = 1,
                 post_hour_et: int = 9, top_n: int = 10, always: bool = False):
        self.conn = conn
        self.org_id = org_id
        self.channels = channels
        self.discord_channel = discord_channel
        self.period_days = period_days
        self.post_hour_et = post_hour_et
        self.top_n = top_n
        self.always = always

    async def poll(self) -> list[PostDraft]:
        try:
            day = datetime.date.today()
            since = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(days=self.period_days)).isoformat()   # ISO-T boundary [R1]
            draft = build_failure_digest_draft(
                self.org_id, self.conn, since, day,
                channels=self.channels, discord_channel=self.discord_channel,
                scheduled_for=morning_utc(day, self.post_hour_et),   # HOUR_ET via scheduled_for [R2]
                top_n=self.top_n, always=self.always)
            return [draft] if draft else []
        except Exception:  # noqa: BLE001 - never break the scheduler tick
            logger.exception("failure-digest poll failed")
            return []
