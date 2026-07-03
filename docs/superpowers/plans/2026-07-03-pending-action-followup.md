# Pending-Conversational-Action (follow-up resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an offer/clarify the bot makes resumable — when the user accepts next turn, execute the stored action deterministically instead of routing the raw acceptance token.

**Architecture:** A per-user, in-memory `PendingAction` (option set) is registered at a shared structured chokepoint that both router paths funnel through (`_answer_decision` for the live `ROUTER_V21=1` path, `_try_structured` for the kill-switch). A deterministic pre-check at the top of `handle()` matches the next message against the pending options and executes the chosen resume via `structured_answer.run()`, bypassing the router. Gated behind `FOLLOWUP_RESUME_ENABLED`.

**Tech Stack:** Python 3.11, discord.py / python-telegram-bot, SQLite, pytest, existing v2 retrieval layer (`router.Route`, `structured_answer`).

**Spec:** `docs/superpowers/specs/2026-07-03-pending-action-followup-design.md` (read §3, §7, §11).

## Global Constraints

- **Deterministic, never guess.** No LLM in the follow-up detection path. Ambiguous → `None` → route normally.
- **Gated + reversible.** Everything behind `FOLLOWUP_RESUME_ENABLED` (default `0`); flag off ⇒ zero behavior change.
- **Layering.** `bot/services/conversation.py` (session layer) imports NO v2 types — options carry plain `skill`/`args` dicts. `v2/core/retrieval/*` returns v2-native `Route`s; the bot layer wraps them.
- **Never raise into the message path.** Any resume error → graceful message, never a crash and never a raw-token fall-through when a selection was recognized.
- **Production runs `ROUTER_V21=1, SHADOW=0`.** Integration tests that exercise the offer path must set `ROUTER_V21=1` so they hit `_answer_decision`, not `_try_structured`.
- **Every change adds its verification Qs to `eval/questions.txt`** (grow-correctness-suite rule).
- Commit messages: no Claude attribution/co-author line.

---

## File Structure

- **Create** `bot/core/pending.py` — `PendingOption`, `PendingAction` dataclasses (pure data, no imports beyond stdlib).
- **Create** `bot/core/followup_match.py` — `match_followup()`, `DECLINE` sentinel, affirmation/negation lexicons.
- **Modify** `bot/services/conversation.py` — `pending_action` field on `ConversationSession`; `set_pending`/`get_pending`/`clear_pending`; `set_mode` clears session on an actual change.
- **Modify** `v2/core/retrieval/router.py` — thread `org_id`/`n`/`org_defaulted` into the `metric_descending_unsupported` Route.
- **Modify** `v2/core/retrieval/structured_answer.py` — `resumable_action(rt)`.
- **Modify** `bot/config.py` — `FOLLOWUP_RESUME_ENABLED` flag.
- **Modify** `bot/core/message_handler.py` — `_register_and_record` side-effect chokepoint (no return-type changes to `_structured_from_route`/`_try_structured`); resume pre-check in `handle()`; `_resume_pending`.
- **Modify** `bot/connectors/telegram_connector.py` — clear pending on live-search button tap (belt-and-suspenders).
- **Tests:** `bot/tests/test_followup_match.py`, `bot/tests/test_pending_session.py`, `v2/tests/test_router_metric_desc_org.py`, `v2/tests/test_resumable_action.py`, `bot/tests/test_followup_resume_integration.py`.

---

## Task 1: Pending-action state + session storage

**Files:**
- Create: `bot/core/pending.py`
- Modify: `bot/services/conversation.py` (add `pending_action` field ~line 28; add methods after `clear_session` ~line 159)
- Test: `bot/tests/test_pending_session.py`

**Interfaces:**
- Produces: `PendingOption(label:str, action:str, payload:dict)`, `PendingAction(options:list[PendingOption], created_at:datetime)`; `ConversationManager.set_pending(user_id, pa)`, `get_pending(user_id)->Optional[PendingAction]`, `clear_pending(user_id)`.

- [ ] **Step 1: Write `bot/core/pending.py`**

```python
"""Pending-conversational-action state — a resumable offer/clarify the bot made,
keyed to a user and consumed on the next turn. Pure data; NO v2 imports (the session
layer must not depend on the retrieval layer). Options carry plain skill/args dicts;
the resume site rebuilds a v2 Route."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PendingOption:
    label: str            # human label; also the match target for pick-1-of-N ("John Smith")
    action: str           # "structured" (only executor wired now; kept for the deferred live-search follow-up)
    payload: dict         # structured: {"skill": str, "args": dict}


@dataclass
class PendingAction:
    options: list[PendingOption]
    created_at: datetime
```

- [ ] **Step 2: Write the failing test** `bot/tests/test_pending_session.py`

```python
from datetime import datetime, timezone
from bot.core.pending import PendingAction, PendingOption
from bot.services.conversation import ConversationManager


def _pa():
    return PendingAction(
        options=[PendingOption("most citations", "structured",
                               {"skill": "top_people_by_metric", "args": {"org_id": 5}})],
        created_at=datetime.now(timezone.utc),
    )


def test_set_get_clear_pending():
    cm = ConversationManager()
    assert cm.get_pending("u1") is None
    cm.set_pending("u1", _pa())
    got = cm.get_pending("u1")
    assert got is not None and got.options[0].payload["skill"] == "top_people_by_metric"
    cm.clear_pending("u1")
    assert cm.get_pending("u1") is None


def test_clear_session_drops_pending():
    cm = ConversationManager()
    cm.set_pending("u1", _pa())
    cm.clear_session("u1")
    assert cm.get_pending("u1") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_pending_session.py -q`
Expected: FAIL (`ConversationManager` has no `set_pending`).

- [ ] **Step 4: Add the field to `ConversationSession`** (`bot/services/conversation.py`, in the `@dataclass class ConversationSession`, after `mode: str = "gsa"` ~line 28)

```python
    mode: str = "gsa"
    pending_action: object = None   # Optional[bot.core.pending.PendingAction]; typed loosely to avoid an import cycle
```

- [ ] **Step 5: Add the manager methods** (`bot/services/conversation.py`, after `clear_session` ~line 159)

```python
    def set_pending(self, user_id: str, pending) -> None:
        """Register a resumable offer/clarify for the user's NEXT turn (one-shot)."""
        session = self.get_or_create_session(user_id)
        session.pending_action = pending

    def get_pending(self, user_id: str):
        session = self.get_session(user_id)
        return session.pending_action if session is not None else None

    def clear_pending(self, user_id: str) -> None:
        session = self.get_session(user_id)
        if session is not None:
            session.pending_action = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_pending_session.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add bot/core/pending.py bot/services/conversation.py bot/tests/test_pending_session.py
git commit -m "feat(followup): pending-action state + session storage"
```

---

## Task 2: Mode switch clears everything (G5)

**Files:**
- Modify: `bot/services/conversation.py` (`set_mode` ~line 167)
- Test: `bot/tests/test_pending_session.py` (append)

**Interfaces:**
- Consumes: Task 1 `set_pending`/`get_pending`.
- Produces: `set_mode` wipes the session (history + pending) on an actual mode change; no-op when unchanged.

- [ ] **Step 1: Write the failing test** (append to `bot/tests/test_pending_session.py`)

```python
def test_mode_switch_clears_session_and_pending():
    cm = ConversationManager()
    cm.add_turn("u1", "user", "who has the lowest citation in ywcc")
    cm.set_pending("u1", _pa())
    cm.set_mode("u1", "free")                       # actual change gsa -> free
    assert cm.get_pending("u1") is None             # pending wiped
    assert cm.get_history("u1") == []               # context wiped
    assert cm.get_mode("u1") == "free"              # new mode stuck (not reset to gsa)


def test_mode_set_same_mode_is_noop():
    cm = ConversationManager()
    cm.add_turn("u1", "user", "hello")
    cm.set_mode("u1", "gsa")                         # unchanged (default is gsa)
    assert cm.get_history("u1") != []               # NOT wiped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_pending_session.py -q`
Expected: FAIL (`test_mode_switch_clears_session_and_pending` — history not wiped).

- [ ] **Step 3: Update `set_mode`** (`bot/services/conversation.py`, replace the existing `set_mode`)

```python
    def set_mode(self, user_id: str, mode: str) -> None:
        # G5 (owner 2026-07-03): a mode switch loses everything — context AND any pending action.
        # Drop the session directly (NOT clear_session, which resets the mode to GSA and would fight
        # the switch we're about to make). No-op when the mode is unchanged.
        current = self.mode_store.get(user_id).value
        from bot.core.modes import Mode
        if Mode(mode).value != current and user_id in self.sessions:
            del self.sessions[user_id]
        self.mode_store.set(user_id, mode)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_pending_session.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/services/conversation.py bot/tests/test_pending_session.py
git commit -m "feat(followup): mode switch wipes session + pending (G5)"
```

---

## Task 3: Deterministic follow-up detector

**Files:**
- Create: `bot/core/followup_match.py`
- Test: `bot/tests/test_followup_match.py`

**Interfaces:**
- Consumes: Task 1 `PendingOption`.
- Produces: `DECLINE` (sentinel object); `match_followup(text:str, options:list[PendingOption]) -> int | object | None` — option index, `DECLINE`, or `None`.

- [ ] **Step 1: Write the failing test** `bot/tests/test_followup_match.py`

```python
from bot.core.pending import PendingOption
from bot.core.followup_match import match_followup, DECLINE


def _opts(*labels):
    return [PendingOption(l, "structured", {"skill": "x", "args": {}}) for l in labels]


ONE = _opts("most citations")
THREE = _opts("Ada Lovelace", "Alan Turing", "Grace Hopper")


def test_affirmation_single_option_selects_zero():
    for t in ["yes", "Yes.", "yeah", "yep", "sure", "ok", "okay", "yes please", "do it", "go ahead"]:
        assert match_followup(t, ONE) == 0, t


def test_affirmation_requires_whole_message():
    assert match_followup("yes but what about MTSM", ONE) is None
    assert match_followup("yes, who is the dean", ONE) is None


def test_negation_returns_decline():
    for t in ["no", "nope", "nah", "never mind", "no thanks"]:
        assert match_followup(t, ONE) is DECLINE, t


def test_affirmation_with_many_options_is_none():
    # "yes" to a pick-1-of-N is ambiguous -> None (never guess)
    assert match_followup("yes", THREE) is None


def test_ordinal_selection():
    assert match_followup("the first", THREE) == 0
    assert match_followup("2", THREE) == 1
    assert match_followup("#3", THREE) == 2
    assert match_followup("option 2", THREE) == 1
    assert match_followup("the fourth", THREE) is None   # out of range


def test_unique_label_selection():
    assert match_followup("Turing", THREE) == 1
    assert match_followup("Grace Hopper", THREE) == 2


def test_ambiguous_or_absent_label_is_none():
    assert match_followup("Smith", THREE) is None        # matches none
    assert match_followup("", ONE) is None
    assert match_followup("what are the office hours", ONE) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_match.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `bot/core/followup_match.py`**

```python
"""Deterministic follow-up matcher — maps a user's reply to one of the pending options.
NO LLM: affirmation/negation are closed lexicons matched against the WHOLE message; a
pick-1-of-N is resolved by ordinal or a UNIQUE label match. Anything ambiguous returns
None (route normally) — never a guess."""

import re

DECLINE = object()   # sentinel: an explicit "no"

_AFFIRM = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "yes please",
    "please do", "do it", "go ahead", "sounds good", "yes do it",
}
_NEGATE = {"no", "nope", "nah", "never mind", "nevermind", "no thanks", "no thank you"}

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.strip(".!?,;:'\"")
    return re.sub(r"\s+", " ", t)


def _ordinal_index(norm: str, n: int):
    # "the first", "first", "option 2", "#3", bare "2"
    m = re.fullmatch(r"(?:the\s+|option\s+|#)?(\d+)", norm)
    if m:
        i = int(m.group(1))
        return i - 1 if 1 <= i <= n else None
    m = re.fullmatch(r"(?:the\s+|option\s+)?([a-z]+)(?:\s+one)?", norm)
    if m and m.group(1) in _ORDINALS:
        i = _ORDINALS[m.group(1)]
        return i - 1 if 1 <= i <= n else None
    return None


def match_followup(text: str, options):
    """Return the selected option index, DECLINE (explicit no), or None (no recognized selection)."""
    if not options:
        return None
    norm = _normalize(text)
    if not norm:
        return None
    if norm in _NEGATE:
        return DECLINE
    if norm in _AFFIRM:
        return 0 if len(options) == 1 else None   # bare "yes" to N options is ambiguous
    # ordinal selection among N
    idx = _ordinal_index(norm, len(options))
    if idx is not None:
        return idx
    # unique label match: exact-equal OR unique substring of exactly one option's label
    labels = [_normalize(o.label) for o in options]
    exact = [i for i, lbl in enumerate(labels) if lbl == norm]
    if len(exact) == 1:
        return exact[0]
    subs = [i for i, lbl in enumerate(labels) if norm in lbl]
    if len(subs) == 1:
        return subs[0]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_match.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add bot/core/followup_match.py bot/tests/test_followup_match.py
git commit -m "feat(followup): deterministic reply->option matcher"
```

---

## Task 4: Thread org into the metric-decline route

**Files:**
- Modify: `v2/core/retrieval/router.py:493-495`
- Test: `v2/tests/test_router_metric_desc_org.py`

**Interfaces:**
- Produces: `Route("metric_descending_unsupported", {field_key, metric_key, org_id, n, org_defaulted})` — `org_id` resolved (root when absent + person cue), so the resume can scope `top_people_by_metric`.

- [ ] **Step 1: Write the failing test** `v2/tests/test_router_metric_desc_org.py`

```python
import sqlite3
from v2.core.retrieval import router
from v2.tests.helpers_kg import make_kg_db   # existing helper used by other router tests


def test_metric_descending_route_carries_org():
    conn = make_kg_db()   # seeds an org "ywcc" + a couple people with citations
    rt = router.route(conn, "who has the lowest citation in ywcc")
    assert rt is not None and rt.skill == "metric_descending_unsupported"
    assert rt.args.get("org_id") is not None          # ywcc resolved + threaded
    assert "n" in rt.args
    assert rt.args.get("org_defaulted") is False


def test_metric_descending_no_org_defaults_root():
    conn = make_kg_db()
    rt = router.route(conn, "who has the fewest citations")
    assert rt is not None and rt.skill == "metric_descending_unsupported"
    assert rt.args.get("org_id") is not None          # defaulted to root
    assert rt.args.get("org_defaulted") is True
```

> If `v2/tests/helpers_kg.make_kg_db` does not exist, reuse the fixture pattern from the existing
> metric-routing test (grep `test_metric` under `v2/tests/`) — do not invent a new fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_router_metric_desc_org.py -q`
Expected: FAIL (`org_id` not in args).

- [ ] **Step 3: Update the route** (`v2/core/retrieval/router.py`, replace the `metric_descending_unsupported` branch ~line 493)

```python
        if person_cue and _DESC_DIR.search(q):
            # Thread the org so the follow-up resume ("most instead") can scope top_people_by_metric.
            # No org named but a person cue → default to the NJIT root (university-wide), mirroring the
            # ascending branch below.
            desc_org, defaulted = org_id, False
            if desc_org is None:
                desc_org = _root_org_id(conn)
                defaulted = True
            return Route("metric_descending_unsupported",
                         {"field_key": field_key, "metric_key": metric.key,
                          "org_id": desc_org, "n": _parse_topn(q), "org_defaulted": defaulted})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest v2/tests/test_router_metric_desc_org.py v2/tests/ -k metric -q`
Expected: PASS (new tests + existing metric tests still green).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/router.py v2/tests/test_router_metric_desc_org.py
git commit -m "feat(followup): thread org/n into metric-decline route for resume scoping"
```

---

## Task 5: `resumable_action` in the structured layer

**Files:**
- Modify: `v2/core/retrieval/structured_answer.py` (add `resumable_action` near `deterministic_suffix`)
- Test: `v2/tests/test_resumable_action.py`

**Interfaces:**
- Consumes: `router.Route`; Task 4's route args. (Reads ONLY `rt` — `person_disambig` candidates live
  in `rt.args["candidates"]`, metric fields in `rt.args`; no `result` needed. This is what lets the
  chokepoint avoid any change to `_structured_from_route`/`_try_structured` return types.)
- Produces: `resumable_action(rt: Route) -> list[tuple[str, Route]] | None`.

- [ ] **Step 1: Write the failing test** `v2/tests/test_resumable_action.py`

```python
from v2.core.retrieval.router import Route
from v2.core.retrieval import structured_answer as sa


def test_metric_decline_produces_top_metric_option():
    rt = Route("metric_descending_unsupported",
               {"field_key": "scholar.citations", "metric_key": "citations",
                "org_id": 5, "n": 1, "org_defaulted": False})
    opts = sa.resumable_action(rt)
    assert opts is not None and len(opts) == 1
    label, route = opts[0]
    assert "citation" in label.lower()
    assert route.skill == "top_people_by_metric"
    assert route.args["org_id"] == 5 and route.args["metric_key"] == "citations"


def test_person_disambig_produces_one_option_per_candidate():
    cands = [{"entity_id": 11, "name": "Ada Lovelace"}, {"entity_id": 22, "name": "Alan Turing"}]
    rt = Route("person_disambig", {"candidates": cands})
    opts = sa.resumable_action(rt)
    assert [l for l, _ in opts] == ["Ada Lovelace", "Alan Turing"]
    assert opts[1][1].skill == "entity_card" and opts[1][1].args["entity_id"] == 22


def test_other_skill_is_not_resumable():
    rt = Route("faculty_in_department", {"org_id": 5})
    assert sa.resumable_action(rt) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_resumable_action.py -q`
Expected: FAIL (`resumable_action` not defined).

- [ ] **Step 3: Add `resumable_action`** (`v2/core/retrieval/structured_answer.py`)

```python
def resumable_action(rt):
    """Given a routed skill, return the resumable option set [(label, Route), ...] for
    offer/clarify skills, else None. Reads ONLY rt (skill + args) — candidates for a
    person_disambig live in rt.args. Owns the single definition of 'what is resumable';
    returns v2-native Routes for the bot layer to wrap."""
    from v2.core.retrieval.router import Route
    skill = rt.skill
    if skill == "metric_descending_unsupported":
        a = rt.args
        noun = _metric_noun(a["metric_key"])
        return [(f"most {noun}",
                 Route("top_people_by_metric",
                       {"org_id": a["org_id"], "field_key": a["field_key"],
                        "metric_key": a["metric_key"], "n": a.get("n", 1),
                        "org_defaulted": a.get("org_defaulted", False)}))]
    if skill == "person_disambig":
        cands = rt.args.get("candidates") or []
        return [(c["name"], Route("entity_card", {"entity_id": c["entity_id"]})) for c in cands] or None
    return None
```

> `_metric_noun` already exists in this module (used at ~line 304). Reuse it — do not redefine.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest v2/tests/test_resumable_action.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/structured_answer.py v2/tests/test_resumable_action.py
git commit -m "feat(followup): resumable_action for metric-decline + person-disambig"
```

---

## Task 6: Rollout flag

**Files:**
- Modify: `bot/config.py` (~line 175, next to `ANSWER_GATE_ENABLED`)

- [ ] **Step 1: Add the flag** (`bot/config.py`, after `ANSWER_GATE_ENABLED`)

```python
# FOLLOWUP_RESUME_ENABLED (default OFF): register a pending-action on offers/clarifies and resume it
# next turn. Off ⇒ no pending is set and the pre-check is skipped (pure current behavior). Flip in
# .env + restart to enable; backout = 0 (or revert).
FOLLOWUP_RESUME_ENABLED = os.getenv("FOLLOWUP_RESUME_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
```

- [ ] **Step 2: Verify it imports**

Run: `.venv/bin/python -c "from bot.config import config; import bot.config as c; print(c.FOLLOWUP_RESUME_ENABLED)"`
Expected: prints `False`.

- [ ] **Step 3: Commit**

```bash
git add bot/config.py
git commit -m "feat(followup): FOLLOWUP_RESUME_ENABLED flag (default off)"
```

---

## Task 7: Shared chokepoint — register pending + record history

**Files:**
- Modify: `bot/core/message_handler.py` — add `_register_and_record` (side-effect); call it from the `_answer_decision` KG branch (`:548-551`) and from `_try_structured` (only when the `:290` caller passes a user). `_structured_from_route` (`:487`) and `_try_structured`'s public return are UNCHANGED; only `_try_structured._run`'s internal tuple carries `rt`.
- Test: `bot/tests/test_followup_resume_integration.py`

**Interfaces:**
- Consumes: Task 5 `structured_answer.resumable_action(rt)`; Task 1 `set_pending`; `bot.core.pending`.
- Produces: `_register_and_record(self, user_id, clean_text, rt, text) -> None` — a **side-effect**
  chokepoint: registers pending (if `resumable_action(rt)` is non-empty AND the flag is on) and writes
  both history turns. Returns nothing; the caller builds its own `MessageResponse`.

> **No return-type changes.** `resumable_action` needs only `rt` (Task 5), so the chokepoint takes a
> `Route` — NOT `result`. Therefore `_structured_from_route` and `_try_structured` keep their **exact
> current return contracts** (3-tuple / `Optional[str]`), and every existing mock of them stays valid.
> `_answer_decision` already holds `decision.skill`/`decision.args` (→ build a `Route`); `_try_structured`
> already builds `rt` inside `_run` (surface it via a local return, internal only). The registration is
> a side-effect both paths invoke after composing their text.

- [ ] **Step 1: Write the failing test** `bot/tests/test_followup_resume_integration.py`

```python
"""Integration: the offer turn registers a pending action + records history, on the LIVE
router path (ROUTER_V21=1). Uses the real assistant against the live DB, then cleans up its
analytics rows (mirrors scratchpad/repro_followup.py)."""
import os, asyncio, pytest

os.environ["ROUTER_V21"] = "1"
os.environ["FOLLOWUP_RESUME_ENABLED"] = "1"


@pytest.mark.integration
def test_offer_registers_pending_and_history():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest

        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        U = "pytest_followup_user"
        cm = asst.message_handler.conversation_manager
        cm.clear_session(U)
        wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
        r = await asst.message_handler.handle(MessageRequest(user_id=U, text="who has the lowest citation in ywcc", platform="telegram"))
        db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
        assert "instead" in (r.text or "").lower()                 # the offer fired
        pa = cm.get_pending(U)
        assert pa is not None and pa.options[0].payload["skill"] == "top_people_by_metric"
        assert len(cm.get_history(U)) == 2                          # Bug 1 fixed: offer turn recorded
        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_resume_integration.py -q`
Expected: FAIL (pending is None / history empty).

- [ ] **Step 3: Add `_register_and_record`** (`bot/core/message_handler.py`, after `_compose_structured` ~line 485)

```python
    def _register_and_record(self, user_id, clean_text, rt, text) -> None:
        """Side-effect chokepoint for BOTH structured paths (_answer_decision + _try_structured):
        register a resumable pending action (flag-gated) and record the answer in history
        (Bug 1 / G6). No return — the caller builds its own MessageResponse."""
        from datetime import datetime, timezone
        from v2.core.retrieval import structured_answer
        from bot.core.pending import PendingAction, PendingOption
        cm = self.conversation_manager
        if cm is None:
            return
        if botcfg.FOLLOWUP_RESUME_ENABLED:
            try:
                resumable = structured_answer.resumable_action(rt)
            except Exception:  # noqa: BLE001 - never break the answer path
                resumable = None
            if resumable:
                cm.set_pending(user_id, PendingAction(
                    options=[PendingOption(label, "structured",
                                           {"skill": r.skill, "args": r.args}) for (label, r) in resumable],
                    created_at=datetime.now(timezone.utc)))
        cm.add_turn(user_id=user_id, role="user", content=clean_text)
        cm.add_turn(user_id=user_id, role="assistant", content=(text or "")[:500])
```

- [ ] **Step 4: Call it from the `_answer_decision` KG branch** (`bot/core/message_handler.py:548-551`). `_structured_from_route` is UNCHANGED (still a 3-tuple); build the `Route` from the decision:

```python
            if ran:
                facts, suffix, deterministic = ran
                text = await self._compose_structured(text, facts, suffix, deterministic)
                from v2.core.retrieval.router import Route
                self._register_and_record(req.user_id, req.text.strip(),
                                          Route(skill=decision.skill, args=dict(decision.args or {})), text)
                return MessageResponse(text=text)
            return await self._rag_pipeline(req, text, INTENT_QUESTION, resolved_query=resolved_query)
```

- [ ] **Step 5: Surface `rt` from `_try_structured._run` (internal only) and register at the `:290` caller.** In `_try_structured._run` (`:446-458`), keep the current 3-tuple but ALSO carry `rt` up — return `(rt, facts, suffix, deterministic)` from `_run`, and in `_try_structured` (`:462-470`) unpack it, compose the text as today, then register when the caller passed a user:

```python
    async def _try_structured(self, text: str, user_id: str | None = None,
                              clean_text: str | None = None) -> "Optional[str]":
        # ... unchanged pregate + _run definition, EXCEPT _run returns (rt, facts, suffix, deterministic) ...
        try:
            ran = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Structured retrieval errored, falling back to RAG: %s", exc)
            return None
        if not ran:
            return None
        rt, facts, suffix, deterministic = ran
        composed = await self._compose_structured(text, facts, suffix, deterministic)
        if user_id is not None:                      # main :290 path → register + record
            self._register_and_record(user_id, clean_text or text, rt, composed)
        return composed
```

Inside `_run`, change its final `return (facts, ...)` to `return (rt, facts, structured_answer.deterministic_suffix(result), structured_answer.is_deterministic(result))` (it already has `rt` and `result` in scope). `_try_structured` STILL returns `Optional[str]` — no external contract change, all existing mocks valid.

- [ ] **Step 6: Pass the user to the `:290` caller only** (`bot/core/message_handler.py:289-292`). The two probe sites (`:250`, `:516`) call `_try_structured(x)` with no user → they skip registration and still get `str`/`None`:

```python
        if mode != "free":
            structured = await self._try_structured(resolved_query, user_id=user_id, clean_text=clean_text)
            if structured is not None:
                return MessageResponse(text=structured)
```

- [ ] **Step 7: Run the integration test**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_resume_integration.py -q`
Expected: PASS (pending set, history has 2 turns).

- [ ] **Step 8: Run the existing structured/handler tests for regressions**

Run: `.venv/bin/python -m pytest bot/tests/test_offer_live_search.py v2/tests/ -q`
Expected: PASS (no regressions in the probe call sites).

- [ ] **Step 9: Commit**

```bash
git add bot/core/message_handler.py bot/tests/test_followup_resume_integration.py
git commit -m "feat(followup): shared chokepoint registers pending + records structured answers in history"
```

---

## Task 8: Resume pre-check + executor

**Files:**
- Modify: `bot/core/message_handler.py` — add the pre-check at the top of `handle()` (after mode resolution ~`:227`, before the context-rewrite call `:228`); add `_resume_pending`.
- Test: `bot/tests/test_followup_resume_integration.py` (append)

**Interfaces:**
- Consumes: Task 3 `match_followup`/`DECLINE`; Task 1 `get_pending`/`clear_pending`; `_structured_from_route`.
- Produces: `_resume_pending(self, option) -> Optional[str]` — runs the option's structured route → composed text, or None.

- [ ] **Step 1: Write the failing tests** (append to `bot/tests/test_followup_resume_integration.py`)

```python
@pytest.mark.integration
def test_yes_resumes_the_metric_ranking():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest

        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        U = "pytest_followup_user2"; cm = asst.message_handler.conversation_manager; cm.clear_session(U)

        async def turn(t):
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            r = await asst.message_handler.handle(MessageRequest(user_id=U, text=t, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            return r

        await turn("who has the lowest citation in ywcc")
        r2 = await turn("yes")
        low = (r2.text or "").lower()
        assert "stem opt" not in low and "immigration" not in low     # NOT the old garbage
        assert "citation" in low or "cited" in low                    # a real ranked answer
        assert cm.get_pending(U) is None                              # consumed
        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_resume_integration.py::test_yes_resumes_the_metric_ranking -q`
Expected: FAIL ("yes" still routes to garbage; no pre-check yet).

- [ ] **Step 3: Add `_resume_pending`** (`bot/core/message_handler.py`, after `_register_and_record`)

```python
    async def _resume_pending(self, option) -> "Optional[str]":
        """Execute a pending option's structured resume, bypassing the router (deterministic).
        Returns composed text, or None on any failure (caller → graceful stop)."""
        if option.action != "structured":
            return None
        skill = option.payload.get("skill"); args = option.payload.get("args") or {}
        try:
            ran = await asyncio.to_thread(self._structured_from_route, skill, args)
        except Exception as exc:  # noqa: BLE001 - never break the message path
            logger.warning("followup resume errored: %s", exc)
            return None
        if not ran:
            return None
        facts, suffix, deterministic = ran        # _structured_from_route returns a 3-tuple (unchanged)
        return await self._compose_structured(option.label, facts, suffix, deterministic)
```

- [ ] **Step 4: Add the pre-check at the top of `handle()`** (`bot/core/message_handler.py`, immediately after `resolved_query = clean_text` and BEFORE the `if mode != "free" and self.ollama` context-rewrite block ~line 227)

```python
        # ── Follow-up resume (thread A) ───────────────────────────────────────
        # A pending offer/clarify from last turn: match this reply to an option and EXECUTE it,
        # instead of routing the raw token. Runs BEFORE context-rewrite so "yes" is never rewritten.
        # One-shot: cleared regardless. Flag-gated.
        if botcfg.FOLLOWUP_RESUME_ENABLED and self.conversation_manager is not None:
            _pending = self.conversation_manager.get_pending(user_id)
            if _pending is not None:
                from bot.core.followup_match import match_followup, DECLINE
                self.conversation_manager.clear_pending(user_id)   # one-shot, before execute (a resume may re-offer)
                _idx = match_followup(clean_text, _pending.options)
                if _idx is DECLINE:
                    ack = "No problem — what else can I help you with?"
                    self.conversation_manager.add_turn(user_id, "user", clean_text)
                    self.conversation_manager.add_turn(user_id, "assistant", ack)
                    return MessageResponse(text=ack)
                if _idx is not None:
                    _resumed = await self._resume_pending(_pending.options[_idx])
                    if _resumed is not None:
                        self.conversation_manager.add_turn(user_id, "user", clean_text)
                        self.conversation_manager.add_turn(user_id, "assistant", _resumed[:500])
                        return MessageResponse(text=_resumed)
                    # recognized but execution FAILED → graceful stop; NEVER fall through to route the token
                    sorry = "Sorry — I couldn't pull that up just now. Could you ask again?"
                    self.conversation_manager.add_turn(user_id, "user", clean_text)
                    self.conversation_manager.add_turn(user_id, "assistant", sorry)
                    return MessageResponse(text=sorry)
                # _idx is None → unrecognized reply → pending already cleared → fall through, route normally
```

- [ ] **Step 5: Run the resume test**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_resume_integration.py -q`
Expected: PASS (offer→"yes"→real ranked answer; pending consumed).

- [ ] **Step 6: Commit**

```bash
git add bot/core/message_handler.py bot/tests/test_followup_resume_integration.py
git commit -m "feat(followup): resume pre-check + executor (the repro now passes)"
```

---

## Task 9: Edge-case + regression tests, eval questions

**Files:**
- Modify: `bot/tests/test_followup_resume_integration.py` (append)
- Modify: `eval/questions.txt`

- [ ] **Step 1: Append edge-case tests** (decline, stale, "yes but…", recognized-but-failed, context-rewrite regressions). Use the same `turn()` harness as Task 8.

```python
@pytest.mark.integration
def test_followup_edge_cases():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest

        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        h = asst.message_handler; cm = h.conversation_manager

        async def turn(U, t):
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            r = await h.handle(MessageRequest(user_id=U, text=t, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            return r

        # decline -> graceful ack, not routed
        Ua = "pf_decline"; cm.clear_session(Ua)
        await turn(Ua, "who has the lowest citation in ywcc")
        r = await turn(Ua, "no")
        assert "no problem" in (r.text or "").lower(); assert cm.get_pending(Ua) is None

        # "yes but ..." -> NOT resumed (routed normally, pending cleared)
        Ub = "pf_yesbut"; cm.clear_session(Ub)
        await turn(Ub, "who has the lowest citation in ywcc")
        r = await turn(Ub, "yes but who is the ywcc dean")
        assert cm.get_pending(Ub) is None  # cleared; answer is the dean route, not the metric ranking

        # stale: unrelated new question supersedes
        Uc = "pf_stale"; cm.clear_session(Uc)
        await turn(Uc, "who has the lowest citation in ywcc")
        r = await turn(Uc, "what are the registrar office hours")
        assert cm.get_pending(Uc) is None

        # recognized-but-failed -> graceful stop, never the raw token
        Ud = "pf_fail"; cm.clear_session(Ud)
        await turn(Ud, "who has the lowest citation in ywcc")
        orig = h._resume_pending
        async def boom(_opt): return None
        h._resume_pending = boom
        try:
            r = await turn(Ud, "yes")
        finally:
            h._resume_pending = orig
        assert "couldn't pull that up" in (r.text or "").lower()

        for U in (Ua, Ub, Uc, Ud): cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())
```

- [ ] **Step 2: Run the edge-case tests**

Run: `.venv/bin/python -m pytest bot/tests/test_followup_resume_integration.py -q`
Expected: PASS (all).

- [ ] **Step 3: Add eval questions** (`eval/questions.txt`, under a new `# followup-resume` header)

```
# followup-resume
who has the lowest citation in ywcc
```

> The 2-turn resume is exercised by the integration test (eval is single-turn); the single question
> documents the offer path is expected to fire cleanly. Note in the commit that the resume itself is
> covered by `test_followup_resume_integration.py`.

- [ ] **Step 4: Commit**

```bash
git add bot/tests/test_followup_resume_integration.py eval/questions.txt
git commit -m "test(followup): edge-cases (decline/stale/yes-but/failed) + eval question"
```

---

## Task 10: Live-search button clears pending (belt-and-suspenders)

**Files:**
- Modify: `bot/connectors/telegram_connector.py:461-498` (`_on_web_search`)

> Per spec §3.4d: the live button only appears on RAG deflections (which set no pending), so there is
> no coexistence in practice — this is defensive, honoring "the button overrides anything."

- [ ] **Step 1: Clear pending before running the live search** (`bot/connectors/telegram_connector.py`, in `_on_web_search`, right before `live = await self.handler.live_search(question_text)` ~line 498)

```python
        cm = getattr(self.handler, "conversation_manager", None)
        if cm is not None:
            cm.clear_pending(self._hash_uid(query.from_user.id))
```

- [ ] **Step 2: Verify Telegram connector still imports + tests pass**

Run: `.venv/bin/python -m pytest bot/tests/test_telegram_web_search.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add bot/connectors/telegram_connector.py
git commit -m "feat(followup): live-search button clears pending (override)"
```

---

## Final verification (before owner sign-off to flip the flag)

- [ ] Full suite: `.venv/bin/python -m pytest bot/tests/ v2/tests/ -q` → green.
- [ ] Manual 2-turn with `FOLLOWUP_RESUME_ENABLED=1 ROUTER_V21=1`: run `scratchpad/repro_followup.py` — turn 2 "yes" now returns the ranked YWCC-by-citations answer (not STEM OPT garbage), history non-empty.
- [ ] Flag-off sanity: with `FOLLOWUP_RESUME_ENABLED=0`, the same repro shows the OLD behavior (proves the gate is inert) — confirms zero behavior change when off.
- [ ] Owner reviews the diff → signs off → set `FOLLOWUP_RESUME_ENABLED=1` in `.env` → `bash scripts/restart.sh`.

## Goals coverage (vs spec §2)

| Goal | Task |
|---|---|
| G1 pending state + offer recorded | 1, 7 |
| G2 deterministic match + execute | 3, 8 |
| G3 general (metric #1 + disambig #3) | 4, 5, 7 |
| G4 drop on non-follow-up | 8 (idx None fall-through) |
| G5 mode switch clears everything | 2 |
| G6 structured answers in history | 7 |
| G7 gated flag | 6 |
| Finding S0 (chokepoint, live path) | 7 |
| Finding #1 (recognized-but-failed) | 8 (graceful stop) + 9 (test) |
| Live-search override | 10 |
