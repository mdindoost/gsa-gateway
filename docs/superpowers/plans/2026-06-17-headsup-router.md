# High-Stakes Heads-Up + Router Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For high-stakes topics (immigration/billing/funding) the bot still answers but appends a one-line "confirm with the authoritative office" heads-up; and the structured router stops hijacking process questions like "who can impeach a GSA officer".

**Architecture:** A tiny `bot/core/headsup.py` (topic table + `apply_headsup(text, question)`) called from `message_handler` after it generates a RAG answer. A positive-identity regex in `router.py` so `officers_in_org` only fires on "who is/are the … officer/president" identity asks. All deterministic and unit-tested; no model, no settings.

**Tech Stack:** Python 3.11, `re`, pytest.

**Design spec:** `docs/superpowers/specs/2026-06-17-answerability-router-design.md`.
**Branch:** `feat/answerability-router` (already created).

---

## File Structure

- Create `bot/core/headsup.py` — `Topic`, `TOPICS`, `match_topic`, `headsup_line`, `apply_headsup`.
- Create `bot/tests/test_headsup.py`.
- Modify `bot/core/message_handler.py` — one `apply_headsup(...)` call after the RAG answer block.
- Modify `v2/core/retrieval/router.py` — positive-identity officer rule.
- Create `v2/tests/test_router_precision.py`.

---

## Task 1: Heads-up module

**Files:**
- Create: `bot/core/headsup.py`
- Test: `bot/tests/test_headsup.py`

- [ ] **Step 1: Write the failing tests**

Create `bot/tests/test_headsup.py`:

```python
from bot.core.headsup import match_topic, headsup_line, apply_headsup


def test_immigration_topics_match():
    for q in ["How do I apply for CPT?", "When do I get my I-20?",
              "How do I apply for OPT before graduation?", "questions about my visa"]:
        t = match_topic(q)
        assert t is not None and t.name == "immigration"


def test_billing_and_funding_match():
    assert match_topic("How do I pay my tuition?").name == "billing"
    assert match_topic("Why do I have a financial hold?").name == "billing"
    assert match_topic("How do I apply for a teaching assistant position?").name == "funding"
    assert match_topic("What is the stipend for a funded PhD student?").name == "funding"


def test_normal_gsa_questions_do_not_match():
    for q in ["Who are the GSA officers?", "What is the travel award?",
              "When is the next GSA event?", "What are the VP of Finance duties?"]:
        assert match_topic(q) is None


def test_headsup_line_names_the_office():
    t = match_topic("How do I apply for CPT?")
    assert "Office of Global Initiatives" in headsup_line(t)


def test_apply_headsup_appends_for_highstakes_only():
    out = apply_headsup("You apply for CPT via OGI.", "How do I apply for CPT?")
    assert out.startswith("You apply for CPT via OGI.")
    assert "confirm with" in out.lower()
    # normal question: unchanged
    same = apply_headsup("The travel award is $900.", "What is the max travel award?")
    assert same == "The travel award is $900."
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest bot/tests/test_headsup.py -q`
Expected: FAIL (ModuleNotFoundError: `bot.core.headsup`).

- [ ] **Step 3: Implement `bot/core/headsup.py`**

Create `bot/core/headsup.py`:

```python
"""High-stakes topic heads-up.

For immigration / billing / funding questions the bot still answers, but appends a one-line
note telling the student to confirm with the authoritative office (rules change and those
offices own them). A small, deterministic seed of the future office-routing (cat-M).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    name: str
    office: str
    patterns: tuple[str, ...]


# Order = priority (first match wins).
TOPICS: tuple[Topic, ...] = (
    Topic("immigration", "Office of Global Initiatives (OGI)",
          ("visa", "i-20", "i20", "cpt", "opt", "sevis", "f-1", "f1 status",
           "work authorization", "immigration")),
    Topic("billing", "Office of the Bursar / Student Accounts",
          ("tuition", "bursar", "billing", "financial hold", "late fee",
           "pay my bill", "payment plan", "refund")),
    Topic("funding", "Office of Graduate Studies or your department",
          ("assistantship", "stipend", "fellowship", "teaching assistant",
           "research assistant", "ta position", "ra position", "tuition waiver")),
)

_COMPILED: tuple[tuple[Topic, "re.Pattern[str]"], ...] = tuple(
    (t, re.compile("|".join(r"\b" + re.escape(p) + r"\b" for p in t.patterns), re.I))
    for t in TOPICS
)


def match_topic(question: str) -> Topic | None:
    for topic, rx in _COMPILED:
        if rx.search(question or ""):
            return topic
    return None


def headsup_line(topic: Topic) -> str:
    return (f"⚠️ _This is based on the GSA's knowledge — please confirm with the "
            f"{topic.office}, since these rules can change and they are the official "
            f"authority._")


def apply_headsup(response_text: str, question: str) -> str:
    """Append the heads-up to an answer when the question is a high-stakes topic; else
    return the answer unchanged."""
    topic = match_topic(question)
    return f"{response_text}\n\n{headsup_line(topic)}" if topic else response_text
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest bot/tests/test_headsup.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add bot/core/headsup.py bot/tests/test_headsup.py
git commit -m "feat(headsup): high-stakes topic table + apply_headsup (immigration/billing/funding)"
```

---

## Task 2: Wire heads-up into the RAG answer path

**Files:**
- Modify: `bot/core/message_handler.py` (right after the RAG answer if/elif/else block — the
  block ends at the deflection `else:` ~line 444, just before "Update conversation memory")

- [ ] **Step 1: Add the import**

At the top of `bot/core/message_handler.py`, with the other `bot.core` imports, add:

```python
from bot.core.headsup import apply_headsup
```

- [ ] **Step 2: Append the heads-up when we answered from chunks**

Find the end of the answer block — the deflection branch:

```python
            else:
                response_text = (
                    "I wasn't able to find specific information about that "
                    "in the GSA knowledge base.\n\n"
                    "For accurate information, please:\n"
                    "- Visit the GSA office at Campus Center 110A (weekdays 11AM–5PM)\n"
                    "- Email us at gsa-pres@njit.edu\n"
                    "- Use /contact to find the right officer"
                )
```

Immediately AFTER that `else:` block (before the `# Update conversation memory` comment), insert:

```python
            # High-stakes heads-up: we still answer, but for immigration/billing/funding
            # questions, tell the student to confirm with the authoritative office. Only when
            # we actually answered from chunks (not the "no info" deflection above).
            if chunks:
                response_text = apply_headsup(response_text, clean_text)
```

- [ ] **Step 3: Verify it imports and a quick smoke check**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -c "import bot.core.message_handler; print('import OK')"`
Expected: `import OK`.

Run a focused behavior check (no full bot needed):

```bash
cd /home/md724/gsa-gateway && .venv/bin/python -c "
from bot.core.headsup import apply_headsup
print(apply_headsup('You request the I-20 from OGI after admission.', 'How do I request my I-20?'))
print('---')
print(apply_headsup('The GSA office is in Campus Center 110A.', 'Where is the GSA office?'))
"
```
Expected: first block ends with the OGI heads-up; second block is unchanged (no heads-up).

- [ ] **Step 4: Run the existing message_handler tests (no regressions)**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest bot/tests/ -q -k "handler or message" 2>&1 | tail -3`
Expected: pass (or "no tests ran" if none match — then run `bot/tests/test_headsup.py` to confirm green).

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add bot/core/message_handler.py
git commit -m "feat(headsup): append high-stakes heads-up after RAG answers"
```

---

## Task 3: Router precision — positive-identity officer rule

**Files:**
- Modify: `v2/core/retrieval/router.py` (the `_OFFICER` definition ~line 41-43 and its use ~line 143)
- Test: `v2/tests/test_router_precision.py`

- [ ] **Step 1: Confirm `_OFFICER` is only used in the officer branch**

Run: `cd /home/md724/gsa-gateway && grep -rn "_OFFICER" v2/ | grep -v "_OFFICER_IDENTITY"`
Expected: only the definition (~line 41) and the one use (~line 143). If used elsewhere, stop and reassess.

- [ ] **Step 2: Write the failing tests**

Create `v2/tests/test_router_precision.py`:

```python
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route


@pytest.fixture(scope="module")
def conn():
    return get_connection("gsa_gateway.db")


@pytest.mark.parametrize("q", [
    "who are the GSA officers",
    "who is the GSA president",
    "who's the VP of Finance",
    "list the GSA officers",
])
def test_identity_questions_route_to_officers(conn, q):
    r = route(conn, q)
    assert r is not None and r.skill == "officers_in_org"


@pytest.mark.parametrize("q", [
    "who can impeach a GSA officer and what vote is needed",
    "what are the duties of the VP of Finance",
    "how do I become a GSA officer",
    "who is eligible to be an officer",
    "how many officers does the GSA have",
])
def test_process_questions_fall_through_to_rag(conn, q):
    r = route(conn, q)
    assert r is None or r.skill != "officers_in_org"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_router_precision.py -q`
Expected: the fall-through tests FAIL (today "who can impeach a GSA officer" routes to `officers_in_org`).

- [ ] **Step 4: Replace the officer matcher with a positive-identity rule**

In `v2/core/retrieval/router.py`, replace the `_OFFICER` definition:

```python
# Officer / governance cue ("who are the OFFICERS", "who is the PRESIDENT / VP finance").
# Deliberately excludes 'professor'/'faculty' so it never hijacks the YWCC faculty branch.
_OFFICER = re.compile(
    r"\b(officers?|e-?board|executive board|president|vice[- ]president|\bvp\b|"
    r"treasurer|secretary|deprep|department representatives?)\b")
```

with a **positive identity** matcher (route only on "who is/are the <title>" / "list the officers";
a bare mention in a process question must NOT route):

```python
# Officer-IDENTITY ask only: "who is/are/'s the <title>", "list/name/show the officers".
# A bare mention of "officer" in a process question (impeach / elect / duties / eligibility)
# must NOT route here — it falls through to RAG (the constitution). Excludes professor/faculty.
_OFFICER_TITLE = (
    r"(?:officers?|e-?board|executive board|president|vice[- ]president|\bvp\b|"
    r"treasurer|secretary|deprep|department representatives?)")
_OFFICER_IDENTITY = re.compile(
    r"(?:who(?:\s+(?:is|are)|'?s)|\b(?:list|name|show))\s+"
    r"(?:the\s+|all\s+|our\s+|current\s+|new\s+|gsa\s+|a\s+)*"
    + _OFFICER_TITLE)
```

Then change the use (~line 143):

```python
    if org_id is not None and _OFFICER.search(q):
        return Route("officers_in_org", {"org_id": org_id})
```

to:

```python
    if org_id is not None and _OFFICER_IDENTITY.search(q):
        return Route("officers_in_org", {"org_id": org_id})
```

- [ ] **Step 5: Run the new tests + existing router tests**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_router_precision.py v2/tests/test_router_officers.py -q 2>&1 | tail -3`
Expected: all PASS. (If `test_router_officers.py` does not exist, run `ls v2/tests/ | grep -i router` and run whatever router test file exists. If a pre-existing officer test now fails on a phrasing the spec intends to route, confirm it matches an identity pattern and adjust the regex; if it asserted a process phrasing should route, that assertion was the bug being fixed — update it.)

- [ ] **Step 6: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/core/retrieval/router.py v2/tests/test_router_precision.py
git commit -m "fix(router): officers route only on identity asks, not bare 'officer' substring"
```

---

## Task 4: Full sweep + end-to-end smoke + finalize

**Files:** none (verification)

- [ ] **Step 1: Run all tests for this work**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest bot/tests/test_headsup.py v2/tests/test_router_precision.py -q 2>&1 | tail -3`
Expected: all green.

- [ ] **Step 2: End-to-end smoke through the structured router**

Run:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python - <<'EOF'
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route
c = get_connection("gsa_gateway.db")
print("impeach ->", route(c, "who can impeach a gsa officer"))      # expect None (falls to RAG)
print("officers ->", route(c, "who are the gsa officers"))          # expect officers_in_org
EOF
```
Expected: impeach → `None`; officers → `Route(skill='officers_in_org', ...)`.

- [ ] **Step 3: Heads-up smoke**

Run:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python -c "
from bot.core.headsup import apply_headsup
for q in ['How do I apply for OPT?', 'How do I pay my tuition?', 'How do I apply for a TA?', 'Who are the GSA officers?']:
    print(q, '=>', 'HEADSUP' if 'confirm with' in apply_headsup('answer.', q).lower() else 'none')
"
```
Expected: OPT/tuition/TA → HEADSUP; officers → none.

- [ ] **Step 4: Update spec status + commit**

In `docs/superpowers/specs/2026-06-17-answerability-router-design.md`, change Status to `Implemented (2026-06-17)`.

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/specs/2026-06-17-answerability-router-design.md
git commit -m "docs: mark heads-up + router spec implemented"
```

- [ ] **Step 5: Report** the test results and that "who can impeach" now falls through to RAG while immigration/billing/funding questions get the heads-up. Then proceed to finishing-a-development-branch (merge + restart per the user's call).
