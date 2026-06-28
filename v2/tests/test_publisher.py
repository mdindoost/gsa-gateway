"""Tests for publishing: signature, publisher, scheduler (Step 6)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.connectors.registry import ConnectorRegistry
from v2.core.connectors.stub_connector import StubConnector
from v2.core.database.schema import create_all
from v2.core.publishing.publisher import PostPublisher
from v2.core.publishing.scheduler import (
    Scheduler, next_occurrence, parse_event_datetime, reminder_fire_time,
)
from v2.core.publishing.signature import SignatureService

FMT = "%Y-%m-%d %H:%M:%S"
PAST = "2020-01-01 00:00:00"
FUTURE = "2999-01-01 00:00:00"

SETTINGS = [
    ("signature.default", "_NJIT Graduate Student Association_", "string"),
    ("signature.variables", json.dumps({
        "org_name": "NJIT Graduate Student Association", "short_name": "NJIT GSA",
        "website": "gsanjit.com"}), "json"),
    ("default.platforms", json.dumps(["discord", "telegram"]), "json"),
    ("default.channel.broadcast", "gsa-announcements", "string"),
    ("default.channel.mathcafe", "gsa-mathcafe", "string"),
    ("org.telegram_channel", "@GSAGateWayNJIT", "string"),
    ("org.groupme_group", "GSAGateWayNJIT", "string"),
]


@pytest.fixture()
def env():
    conn = create_all(":memory:")  # now includes a v1-compatible `events` table
    org = conn.execute(
        "INSERT INTO organizations(name,slug,type) VALUES('GSA','gsa','gsa')"
    ).lastrowid
    for key, value, vtype in SETTINGS:
        conn.execute(
            "INSERT INTO settings(org_id,key,value,type) VALUES(?,?,?,?)",
            (org, key, value, vtype),
        )
    conn.commit()
    registry = ConnectorRegistry(conn=conn)
    registry.register(StubConnector("discord"))
    registry.register(StubConnector("telegram"))
    registry.register(StubConnector("groupme"))
    sigs = SignatureService(conn)
    publisher = PostPublisher(conn, conn, registry, sigs)
    scheduler = Scheduler(conn, conn, publisher)
    return dict(conn=conn, org=org, registry=registry, sigs=sigs,
                publisher=publisher, scheduler=scheduler)


def _insert_post(conn, org, **kw):
    cols = {"org_id": org, "type": "one_time", "content": "hello",
            "channels": "[]", "status": "scheduled"}
    cols.update(kw)
    keys = ",".join(cols)
    qs = ",".join("?" * len(cols))
    return conn.execute(
        f"INSERT INTO posts({keys}) VALUES({qs})", list(cols.values())
    ).lastrowid


# ── signature ────────────────────────────────────────────────────────────────

def test_signature_default(env):
    assert env["sigs"].render(env["org"]) == "_NJIT Graduate Student Association_"


def test_signature_template_vars(env):
    out = env["sigs"].render(env["org"], "{short_name} • {website}")
    assert out == "NJIT GSA • gsanjit.com"


def test_signature_unknown_var_left_intact(env):
    assert env["sigs"].render(env["org"], "{short_name} {nope}") == "NJIT GSA {nope}"


# ── publisher ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_due_sends_and_marks_sent(env):
    conn, org = env["conn"], env["org"]
    pid = _insert_post(conn, org, scheduled_for=PAST, content="Town hall tonight")
    conn.commit()
    summary = await env["publisher"].publish_due()
    assert summary == {"published": 1, "sent": 1, "failed": 0}
    row = conn.execute("SELECT status, sent_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "sent" and row["sent_at"]
    # two platforms logged
    n = conn.execute("SELECT COUNT(*) FROM post_deliveries WHERE post_id=?", (pid,)).fetchone()[0]
    assert n == 2


@pytest.mark.asyncio
async def test_future_post_not_published(env):
    conn, org = env["conn"], env["org"]
    pid = _insert_post(conn, org, scheduled_for=FUTURE)
    conn.commit()
    await env["publisher"].publish_due(now="2021-01-01 00:00:00")
    status = conn.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()["status"]
    assert status == "scheduled"


@pytest.mark.asyncio
async def test_channel_resolution_from_settings(env):
    conn, org = env["conn"], env["org"]
    _insert_post(conn, org, type="mathcafe", scheduled_for=PAST, content="fact")
    conn.commit()
    await env["publisher"].publish_due()
    dch = env["registry"].get("discord").calls[0]["channel"]
    tch = env["registry"].get("telegram").calls[0]["channel"]
    assert dch == "gsa-mathcafe"          # mapped from post type
    assert tch == "@GSAGateWayNJIT"        # org telegram channel


@pytest.mark.asyncio
async def test_groupme_channel_from_settings(env):
    conn, org = env["conn"], env["org"]
    _insert_post(conn, org, scheduled_for=PAST, content="GroupMe hello",
                 channels='["groupme"]')
    conn.commit()
    await env["publisher"].publish_due()
    gch = env["registry"].get("groupme").calls[0]["channel"]
    assert gch == "GSAGateWayNJIT"


@pytest.mark.asyncio
async def test_signature_applied_to_delivered_content(env):
    conn, org = env["conn"], env["org"]
    _insert_post(conn, org, scheduled_for=PAST, content="Body")
    conn.commit()
    await env["publisher"].publish_due()
    sent = env["registry"].get("discord").calls[0]["content"]
    assert sent.endswith("_NJIT Graduate Student Association_")


@pytest.mark.asyncio
async def test_failed_delivery_marks_post_failed(env):
    conn, org = env["conn"], env["org"]
    # both connectors fail
    env["registry"]._connectors["discord"]._fail = True
    env["registry"]._connectors["telegram"]._fail = True
    pid = _insert_post(conn, org, scheduled_for=PAST)
    conn.commit()
    await env["publisher"].publish_due()
    status = conn.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()["status"]
    assert status == "failed"


# ── scheduler: recurring templates ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_recurring_template_materializes_and_advances(env):
    conn, org = env["conn"], env["org"]
    tid = conn.execute(
        "INSERT INTO post_templates(org_id,name,content,post_type,recurrence,channels,"
        "discord_channel,next_run_at) VALUES(?,?,?,?,?,?,?,?)",
        (org, "MathCafe Daily", "Daily fact", "mathcafe",
         json.dumps({"freq": "daily", "time": "09:00"}),
         json.dumps(["discord"]), "gsa-mathcafe", PAST),
    ).lastrowid
    conn.commit()

    result = await env["scheduler"].tick()
    assert result["templates_materialized"] == 1
    # a post was created from the template AND published
    post = conn.execute(
        "SELECT type, source_type, source_id, status FROM posts WHERE source_id=?", (tid,)
    ).fetchone()
    assert post["type"] == "mathcafe" and post["source_type"] == "template"
    assert post["status"] == "sent"
    # next_run_at advanced into the future
    nxt = conn.execute("SELECT next_run_at FROM post_templates WHERE id=?", (tid,)).fetchone()[0]
    assert nxt > datetime.now().strftime(FMT)


@pytest.mark.asyncio
async def test_event_driven_template_does_not_advance(env):
    conn, org = env["conn"], env["org"]
    tid = conn.execute(
        "INSERT INTO post_templates(org_id,name,content,post_type,recurrence,next_run_at) "
        "VALUES(?,?,?,?,?,?)",
        (org, "World Cup", "live", "worldcup",
         json.dumps({"freq": "event_driven"}), PAST),
    ).lastrowid
    conn.commit()
    await env["scheduler"].tick()
    nxt = conn.execute("SELECT next_run_at FROM post_templates WHERE id=?", (tid,)).fetchone()[0]
    assert nxt is None  # no further auto-runs


# ── scheduler: event reminders ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_reminder_materializes_when_due(env):
    conn, org = env["conn"], env["org"]
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    eid = conn.execute(
        "INSERT INTO events(name,date,time,location,created_at,created_by,org_id) "
        "VALUES(?,?,?,?,?,?,?)",
        ("Town Hall", tomorrow, "09:00", "CC110A", PAST, "test", org),
    ).lastrowid
    # reminder 7 days before -> fire time already past -> due now
    rid = conn.execute(
        "INSERT INTO event_reminders(event_id,offset_value,offset_unit,channels) "
        "VALUES(?,?,?,?)",
        (eid, 7, "days", json.dumps(["discord"])),
    ).lastrowid
    conn.commit()

    n = env["scheduler"].materialize_event_reminders(datetime.now())
    assert n == 1
    linked = conn.execute("SELECT post_id FROM event_reminders WHERE id=?", (rid,)).fetchone()[0]
    assert linked is not None
    post = conn.execute("SELECT type, status FROM posts WHERE id=?", (linked,)).fetchone()
    assert post["type"] == "event_reminder"
    # not double-materialized on a second tick
    assert env["scheduler"].materialize_event_reminders(datetime.now()) == 0


# ── recurrence + time math (unit) ────────────────────────────────────────────

def test_next_occurrence_daily():
    base = datetime(2026, 6, 8, 12, 0, 0)
    nxt = next_occurrence({"freq": "daily", "time": "09:00"}, base)
    assert nxt == datetime(2026, 6, 9, 9, 0, 0)


def test_next_occurrence_weekly_picks_next_listed_day():
    base = datetime(2026, 6, 8, 12, 0, 0)  # Monday
    nxt = next_occurrence({"freq": "weekly", "days_of_week": [4], "time": "17:00"}, base)
    assert nxt.weekday() == 4 and nxt.hour == 17  # Friday


def test_next_occurrence_monthly():
    base = datetime(2026, 1, 31, 0, 0, 0)
    nxt = next_occurrence({"freq": "monthly", "time": "08:00"}, base)
    assert nxt.month == 2 and nxt.day == 28  # clamps to Feb


def test_next_occurrence_event_driven_is_none():
    assert next_occurrence({"freq": "event_driven"}, datetime.now()) is None


def test_parse_event_datetime_variants():
    assert parse_event_datetime("2026-06-12", "1:00 PM").hour == 13
    assert parse_event_datetime("2026-06-12", "10 AM").hour == 10
    assert parse_event_datetime("2026-06-12", "TBD").hour == 9  # default


def test_reminder_fire_time():
    fire = reminder_fire_time("2026-06-12", "09:00", 1, "days")
    assert fire == datetime(2026, 6, 11, 9, 0, 0)
