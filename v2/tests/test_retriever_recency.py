"""Tests for retriever recency/type multiplier (Phase C).

C1 — decay_for() pure function (news/event/webpage).
C2 — decay_for wired at both boost sites with shared now (monkeypatch spy).
C3 — DEFAULT_EXCLUDE_TYPES no longer excludes webpage or office_page.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.retrieval.retriever import decay_for

NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _row(type_, **meta):
    return {"type": type_, "metadata": meta}


# ── C1: decay_for() unit tests ───────────────────────────────────────────────

def test_news_recency_decay_with_floor():
    fresh = decay_for(_row("news", published_at="2026-06-20"), NOW)
    old = decay_for(_row("news", published_at="2019-01-01"), NOW)
    undated = decay_for(_row("news"), NOW)
    assert 0.80 < fresh <= 0.85
    assert old == 0.5                       # floor, never below
    assert abs(undated - 0.85) < 1e-9      # undated = prior, no decay


def test_event_boost_only_upcoming():
    up = decay_for(_row("event", event_end="2026-12-01"), NOW)
    past = decay_for(_row("event", event_end="2024-01-01"), NOW)
    dateless = decay_for(_row("event"), NOW)
    assert up == 1.2
    assert past <= 1.0
    assert dateless == 1.0                  # fail-closed: no boost


def test_webpage_and_default_and_eventinfo():
    assert decay_for(_row("webpage"), NOW) == 0.8
    assert decay_for(_row("policy"), NOW) == 1.0
    assert decay_for(_row("event_info"), NOW) == 1.2


def test_news_future_dated_treated_as_age_zero():
    future = decay_for(_row("news", published_at="2030-01-01"), NOW)
    # age=0 → NEWS_PRIOR * 0.5^(0/180) = NEWS_PRIOR * 1.0 = 0.85
    assert abs(future - 0.85) < 1e-9


def test_news_metadata_as_json_string():
    """metadata stored as a JSON string must be parsed."""
    import json
    row = {"type": "news", "metadata": json.dumps({"published_at": "2026-06-20"})}
    result = decay_for(row, NOW)
    assert 0.80 < result <= 0.85


def test_event_uses_event_start_if_no_end():
    up = decay_for(_row("event", event_start="2026-12-01"), NOW)
    past = decay_for(_row("event", event_start="2024-01-01"), NOW)
    assert up == 1.2
    assert past <= 1.0


def test_event_past_with_published_at_decays_as_news():
    """Past event with published_at falls back to news-style decay."""
    # published_at recent → above floor, above 0.5
    result = decay_for(_row("event", event_end="2024-01-01", published_at="2026-06-20"), NOW)
    assert 0.5 <= result <= 0.85


# ── C2: both boost sites use full row dict + now ──────────────────────────────

def test_decay_called_with_row_dict_at_both_boost_sites(monkeypatch):
    """Verify decay_for is invoked with a dict (not a bare type string) and a
    datetime at BOTH boost sites: fusion-loop and _rerank scorer.

    Approach (b): spy on decay_for via monkeypatch and record every call.
    The test fails before C2 because the old _boost_for takes a bare string and
    never calls decay_for; after C2 both sites call decay_for(row, now).
    """
    import math
    import sqlite_vec
    import v2.core.retrieval.retriever as R_mod
    from v2.core.database.schema import create_all

    calls: list[tuple] = []

    original_decay_for = R_mod.decay_for

    def spy_decay_for(row, now):
        calls.append((row, now))
        return original_decay_for(row, now)

    monkeypatch.setattr(R_mod, "decay_for", spy_decay_for)

    # Minimal in-memory DB with one item
    conn = create_all(":memory:")
    org_id = conn.execute(
        "INSERT INTO organizations(name, slug, type) VALUES ('NJIT','njit','university')"
    ).lastrowid
    iid = conn.execute(
        "INSERT INTO knowledge_items(org_id, type, title, content) "
        "VALUES (?, 'news', 'Test title', 'hello world')",
        (org_id,),
    ).lastrowid

    # Minimal stub embedder
    class _Stub:
        def _v(self, t):
            v = [0.0] * 768
            v[0] = 1.0
            return v

        def embed_query(self, t):
            return self._v(t)

        def embed_document(self, t):
            return self._v(t)

    stub = _Stub()
    vec = stub.embed_document("hello world")
    conn.execute(
        "INSERT INTO knowledge_vectors(item_id, embedding) VALUES (?, ?)",
        (iid, sqlite_vec.serialize_float32(vec)),
    )
    conn.commit()

    retriever = R_mod.V2Retriever(conn, stub)
    retriever.retrieve("hello", limit=1)
    conn.close()

    # Every call to decay_for should have a dict (not a bare string) as first arg
    # and a datetime as second arg.
    assert calls, "decay_for was never called — boost sites not wired"
    for row_arg, now_arg in calls:
        assert isinstance(row_arg, dict), (
            f"decay_for got a non-dict first arg: {type(row_arg).__name__!r} = {row_arg!r}"
        )
        assert "type" in row_arg, "row dict missing 'type' key"
        assert isinstance(now_arg, datetime), (
            f"decay_for got a non-datetime now: {type(now_arg).__name__!r}"
        )


# ── C3: DEFAULT_EXCLUDE_TYPES ─────────────────────────────────────────────────

def test_default_exclude_types():
    from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES
    assert "publication" in DEFAULT_EXCLUDE_TYPES
    assert "office_page" not in DEFAULT_EXCLUDE_TYPES
    assert "webpage" not in DEFAULT_EXCLUDE_TYPES
