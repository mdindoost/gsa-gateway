# Query-Correction C+A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert router-`None` structured-debt queries ("who run cs", "boss of ywcc", "top cited prof in cs") into KG-routed answers via a deterministic acronym dictionary + an org-type-aware router "leader rule" + closed-lexicon typo tolerance — no LLM.

**Architecture:** Two deterministic pieces. (A) A curated acronym dictionary augments the query at the top of `handle()`. (C) The router (`v2/core/retrieval/router.py`) gains a leader rule that maps `run`/`boss`/`head`/`president-of-<unit>` to the org-type-appropriate role (dept→chair, college→dean, club/gsa→officers) resolved from the org node's actual type, plus edit-≤2 typo tolerance into the CLOSED role/org/metric lexicons. The LLM-rewrite apparatus in §§4–13 of the spec is DROPPED (spec G6).

**Tech Stack:** Python 3.11, sqlite3, pytest. No new dependencies. No LLM.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-29-query-correction-salvage-design.md` — build to **§14 (rev-5 C+A)**; §§4–13 are the DEFERRED LLM path, do NOT build them.
- **Flag:** all new behavior gated by `QUERY_CORRECT_ENABLED` (default OFF; kill = `0` + restart). Read at call time via a testable helper (not a frozen module-level constant).
- **GSA-equal** ([[feedback_gsa_equal_not_privileged]]): the leader mapping is ORG-TYPE-driven — no GSA thumb, no alias table.
- **No-LLM router preserved**; deterministic, zero-latency on the hot path.
- **Preserve existing guards:** `role_is_org` (router.py ~:858) and `_LEADERSHIP_PROCESS` (~:848) must keep office-hours / process queries OUT of person lookups.
- **AUGMENT, never expand-in-place** (dictionary keeps the bare token + appends the expansion).
- **Run the GOLD/EVAL set, not spot-checks** (the 2026-07-03 lesson): `bash scripts/eval.sh` + router gold suites must show no regression before any merge.
- **Grow the suite** ([[feedback_grow_correctness_suite]]): every new rule adds its Qs to `eval/questions.txt`.
- Org types in the live DB (for the leader mapping): `department`, `college`, `school`, `club`, `gsa`, `office`, `unit`, `program`, `custom`, `university`(root).
- Re-locate every cited `router.py`/`message_handler.py` line number by SYMBOL at build time — numbers drift.

---

### Task 1: Config flag `QUERY_CORRECT_ENABLED` + testable gate helper

**Files:**
- Modify: `bot/config.py` (add the flag next to `ANSWER_GATE_ENABLED`/`LIVE_ENABLED`, ~:154/:180)
- Create: `v2/core/retrieval/query_correct.py` (new module; starts with the gate helper)
- Test: `v2/tests/test_query_correct_config.py`

**Interfaces:**
- Produces: `botcfg.QUERY_CORRECT_ENABLED: bool`; `v2.core.retrieval.query_correct.enabled() -> bool` (reads the env at CALL time so tests + a restart flip it without reimport).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_config.py
import importlib, os
from v2.core.retrieval import query_correct

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    assert query_correct.enabled() is False

def test_flag_on(monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    assert query_correct.enabled() is True

def test_botcfg_exposes_flag(monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    import bot.config as botcfg; importlib.reload(botcfg)
    assert botcfg.QUERY_CORRECT_ENABLED is True
```

- [ ] **Step 2: Run test to verify it fails.** `python -m pytest v2/tests/test_query_correct_config.py -v` — Expected: FAIL (module/attr missing).

- [ ] **Step 3: Add the flag + helper.** In `bot/config.py`, mirroring the `LIVE_ENABLED` line:

```python
QUERY_CORRECT_ENABLED = os.getenv("QUERY_CORRECT_ENABLED", "0") == "1"
```

Create `v2/core/retrieval/query_correct.py`:

```python
"""Deterministic query correction (C+A): acronym dictionary + router-leader-rule support.
Spec §14. No LLM. Gated by QUERY_CORRECT_ENABLED (read at call time)."""
from __future__ import annotations
import os

def enabled() -> bool:
    return os.getenv("QUERY_CORRECT_ENABLED", "0") == "1"
```

- [ ] **Step 4: Run test to verify it passes.** `python -m pytest v2/tests/test_query_correct_config.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add bot/config.py v2/core/retrieval/query_correct.py v2/tests/test_query_correct_config.py && git commit -m "feat(query-correct): QUERY_CORRECT_ENABLED flag + call-time gate helper (default off)"`

---

### Task 2: Acronym dictionary — `augment_acronyms`

**Files:**
- Modify: `v2/core/retrieval/query_correct.py`
- Test: `v2/tests/test_query_correct_acronyms.py`

**Interfaces:**
- Produces: `ACRONYMS: dict[str, str]`; `augment_acronyms(text: str, protected: set[str] | None = None) -> str` — whole-word, case-insensitive; for each matched abbreviation, AUGMENT (keep the bare token, append the expansion after it); never expands a token in `protected`; returns text unchanged when nothing matches.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_acronyms.py
from v2.core.retrieval.query_correct import augment_acronyms

def test_augment_keeps_bare_and_appends():
    assert augment_acronyms("what is gsa") == "what is gsa graduate student association"

def test_augment_metric_words():
    # the metric class the dictionary owns (spec §14.1)
    assert augment_acronyms("top cited prof in computer sci") == \
        "top cited prof professor in computer sci science"

def test_augment_noop_when_no_abbrev():
    assert augment_acronyms("who is the dean of engineering") == "who is the dean of engineering"

def test_augment_case_insensitive_preserves_bare():
    assert augment_acronyms("What is GSA").lower() == "what is gsa graduate student association"

def test_augment_skips_protected():
    assert augment_acronyms("prof wang", protected={"prof"}) == "prof wang"
```

- [ ] **Step 2: Run test to verify it fails.** `python -m pytest v2/tests/test_query_correct_acronyms.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement** in `v2/core/retrieval/query_correct.py`:

```python
import re

# Curated, reviewed, GSA-equal. Whole-word. The LLM is forbidden acronyms (spec §14.1).
ACRONYMS: dict[str, str] = {
    "gsa": "graduate student association",
    "dept": "department", "dep": "department",
    "prof": "professor",
    "cs": "computer science",
    "sci": "science",
    "eng": "engineering",
    "ece": "electrical and computer engineering",
    "uni": "university",
}
_ACRONYM_RX = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, ACRONYMS), key=len, reverse=True)) + r")\b", re.I)

def augment_acronyms(text: str, protected: set[str] | None = None) -> str:
    protected = protected or set()
    def _sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok.lower() in protected:
            return tok
        return f"{tok} {ACRONYMS[tok.lower()]}"
    return _ACRONYM_RX.sub(_sub, text)
```

- [ ] **Step 4: Run test to verify it passes.** `python -m pytest v2/tests/test_query_correct_acronyms.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/query_correct.py v2/tests/test_query_correct_acronyms.py && git commit -m "feat(query-correct): augment_acronyms dictionary (AUGMENT, protected-aware)"`

---

### Task 3: Wire the dictionary into `handle()` (gated)

**Files:**
- Modify: `bot/core/message_handler.py` (top of `handle()`, after the context-rewrite / where `clean_text` and `base_q` are set — re-locate by symbol)
- Test: `v2/tests/test_query_correct_wiring.py`

**Interfaces:**
- Consumes: `query_correct.enabled()`, `augment_acronyms` (Task 1/2).
- Produces: the routing/retrieval query is the augmented string when the flag is ON; `clean_text` (display/log) stays the ORIGINAL. Off → byte-identical to today.

- [ ] **Step 1: Read the current top of `handle()`.** `grep -n "def handle\|clean_text =\|base_q =" bot/core/message_handler.py`. Identify where the query used for routing/retrieval is first set. `clean_text` (display) must remain the untouched original.

- [ ] **Step 2: Write the failing test**

```python
# v2/tests/test_query_correct_wiring.py
from v2.core.retrieval.query_correct import augment_acronyms

def test_off_is_identity(monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    # augment is only APPLIED when enabled(); the raw helper is still identity on no-match
    assert augment_acronyms("who is the chair of cs") == "who is the chair of cs computer science"

def test_protected_name_not_expanded():
    # a surname that collides with an abbrev is protected via nodes tokens (passed by handle())
    assert augment_acronyms("prof eng", protected={"eng"}) == "prof professor eng"
```

(Behavioral wiring is covered end-to-end by the Task 7 eval smoke; this task's unit test pins the protected-set contract the handler passes.)

- [ ] **Step 3: Run test to verify it fails/passes appropriately.** `python -m pytest v2/tests/test_query_correct_wiring.py -v`.

- [ ] **Step 4: Wire it.** At the top of `handle()`, after `clean_text` is set and before routing/retrieval, gate on the flag:

```python
from v2.core.retrieval import query_correct
# ... inside handle(), clean_text = <original, untouched> ...
routing_q = clean_text
if query_correct.enabled():
    routing_q = query_correct.augment_acronyms(clean_text, protected=self._name_tokens())
# feed routing_q to _try_structured / router / _rag_pipeline's base_q; clean_text stays original.
```

Add `_name_tokens()` (lazy-cached set of `nodes` person-name tokens len>2, lowercased) so a surname colliding with an abbrev is never expanded. If a name-token loader already exists for another feature, reuse it.

- [ ] **Step 5: Run tests + a targeted handler regression.** `python -m pytest v2/tests/test_query_correct_wiring.py bot/tests/ -k "handler or message" -v` — Expected: PASS, no new failures.

- [ ] **Step 6: Commit.** `git add bot/core/message_handler.py v2/tests/test_query_correct_wiring.py && git commit -m "feat(query-correct): wire acronym dictionary at top of handle() (gated, original preserved)"`

---

### Task 4: Leader-role mapping helper (pure) + `_LEADER_INTENT`

**Files:**
- Modify: `v2/core/retrieval/router.py` (add near `ORG_TYPE_LEVEL`/`_LEADERSHIP_PROCESS`)
- Test: `v2/tests/test_router_leader_rule.py` (helper unit portion)

**Interfaces:**
- Produces: `_LEADER_INTENT: re.Pattern` (matches leadership-intent phrasings NOT in `_ROLE_VOCAB`); `_leader_role_for_org(conn, org_id) -> tuple[str, str] | None` — returns `("people_by_role", role_head)` for dept(chair)/college·school(dean)/university(president), `("officers_in_org", "")` for club/gsa, or `None` for org types the rule should not force (office/unit/program/custom).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_router_leader_rule.py
import sqlite3
from v2.core.retrieval import router

def _mk(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, type TEXT, parent_id INTEGER)")
    conn.executemany("INSERT INTO organizations (id,type,parent_id) VALUES (?,?,?)",
        [(1,"university",None),(2,"college",1),(3,"department",2),(4,"club",1),(5,"gsa",1)])
    return conn

def test_leader_intent_matches_slang():
    assert router._LEADER_INTENT.search("who run cs")
    assert router._LEADER_INTENT.search("boss of ywcc")
    assert router._LEADER_INTENT.search("who president cs")
    assert not router._LEADER_INTENT.search("who is the chair of cs")   # role-vocab path owns this

def test_leader_role_by_org_type(tmp_path):
    conn = _mk(tmp_path)
    assert router._leader_role_for_org(conn, 3) == ("people_by_role", "chair")   # department
    assert router._leader_role_for_org(conn, 2) == ("people_by_role", "dean")    # college
    assert router._leader_role_for_org(conn, 1) == ("people_by_role", "president")# university
    assert router._leader_role_for_org(conn, 4) == ("officers_in_org", "")       # club
    assert router._leader_role_for_org(conn, 5) == ("officers_in_org", "")       # gsa
```

- [ ] **Step 2: Run test to verify it fails.** `python -m pytest v2/tests/test_router_leader_rule.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement** in `router.py`:

```python
# Leadership-INTENT phrasings that are NOT in _ROLE_VOCAB (so the role branch misses them).
# "who runs/run", "boss of", "head(s) of", "who leads", "in charge of", "president of <unit>".
_LEADER_INTENT = re.compile(
    r"\bwho\s+runs?\b|\bruns?\b|\bboss\s+of\b|\bheads?\s+of\b|\bwho\s+leads?\b|"
    r"\bin\s+charge\s+of\b|\bpresident\s+of\b|\bwho\s+president\b", re.I)

_LEADER_ROLE_BY_TYPE = {"department": "chair", "college": "dean", "school": "dean",
                        "university": "president"}

def _leader_role_for_org(conn, org_id):
    row = conn.execute("SELECT type FROM organizations WHERE id=?", (org_id,)).fetchone()
    if row is None:
        return None
    otype = row[0]
    if otype in ("club", "gsa"):
        return ("officers_in_org", "")
    role = _LEADER_ROLE_BY_TYPE.get(otype)
    return ("people_by_role", role) if role else None
```

- [ ] **Step 4: Run test to verify it passes.** `python -m pytest v2/tests/test_router_leader_rule.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/router.py v2/tests/test_router_leader_rule.py && git commit -m "feat(router): leader-intent regex + org-type->role helper (pure)"`

---

### Task 5: Wire the leader rule into `route()` (gated)

**Files:**
- Modify: `v2/core/retrieval/router.py` (inside `route()`, in the role region — after the `_LEADERSHIP_PROCESS` gate, before/alongside the `_ROLE_VOCAB_RX` branch, ~:848)
- Test: `v2/tests/test_router_leader_rule.py` (append integration cases)

**Interfaces:**
- Consumes: `_LEADER_INTENT`, `_leader_role_for_org` (Task 4); `_find_org`, `Route`, the `role_is_org`/`explicit` machinery already in `route()`; `query_correct.enabled()`.
- Produces: `route(conn, "who run cs")` → `Route("people_by_role", {"role_head":"chair","org_id":<cs>})`; `route(conn, "boss of ywcc")` → dean; club/gsa → `officers_in_org`. Off-flag → unchanged (`None` as today).

- [ ] **Step 1: Write the failing test** (uses the live DB via the repo's router fixture pattern — mirror an existing `v2/tests/test_router*.py` that opens `gsa_gateway.db` read-only; assert the routed skill + org).

```python
# append to v2/tests/test_router_leader_rule.py — integration (live DB), flag ON
import os, pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route

@pytest.fixture
def conn():
    c = get_connection("gsa_gateway.db"); yield c; c.close()

def test_who_run_cs_routes_chair(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "who run cs computer science")   # dictionary already expanded cs
    assert r is not None and r.skill == "people_by_role" and r.args.get("role_head") == "chair"

def test_boss_of_ywcc_routes_dean(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "boss of ywcc")
    assert r is not None and r.skill == "people_by_role" and r.args.get("role_head") == "dean"

def test_leader_rule_off_by_flag(conn, monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    assert route(conn, "who run cs computer science") is None   # unchanged when off

def test_registrar_office_hours_still_office(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "registrar office hours")
    assert r is None or r.skill != "people_by_role"   # role_is_org guard held
```

- [ ] **Step 2: Run test to verify it fails.** `python -m pytest v2/tests/test_router_leader_rule.py -v` — Expected: the integration cases FAIL.

- [ ] **Step 3: Implement.** Insert at the top of the existing `if not _LEADERSHIP_PROCESS.search(q):` block (before `rm = _ROLE_VOCAB_RX.search(q)`), so an explicit role word still wins if present:

```python
        # Leader rule (spec §14 C-1): map run/boss/head/president-of-<unit> to the org-type role.
        from v2.core.retrieval import query_correct
        if (query_correct.enabled() and _LEADER_INTENT.search(q) and org_id is not None):
            role_is_org = bool(org_phrase and any(
                w in org_phrase.lower() for w in ("president", "head", "chair", "dean")))
            if not role_is_org:
                mapped = _leader_role_for_org(conn, org_id)
                if mapped is not None:
                    skill, role_head = mapped
                    if skill == "officers_in_org":
                        return Route("officers_in_org", {"org_id": org_id})
                    return Route("people_by_role", {"role_head": role_head, "org_id": org_id})
```

Note: `_find_org` already resolved `org_id`/`org_phrase` at the top of `route()`; the `president of <dept>` disambiguation is handled by `_leader_role_for_org` (a dept resolves to chair, never the Office of the President), and the `role_is_org` check keeps "president office hours"-style asks out.

- [ ] **Step 4: Run test to verify it passes.** `python -m pytest v2/tests/test_router_leader_rule.py -v` — Expected: PASS.

- [ ] **Step 5: Run the router gold suites (no-regression).** `python -m pytest v2/tests/ -k "router or gold" -v` — Expected: no NEW failures vs `main` (record any pre-existing ones).

- [ ] **Step 6: Commit.** `git add v2/core/retrieval/router.py v2/tests/test_router_leader_rule.py && git commit -m "feat(router): wire org-type-aware leader rule into route() (gated, role_is_org preserved)"`

---

### Task 6: Closed-lexicon edit-≤2 typo tolerance (role / org / metric)

**Files:**
- Modify: `v2/core/retrieval/query_correct.py` (add `closed_lexicon_fix`)
- Modify: `v2/core/retrieval/router.py` (apply to role/org/metric token matching, gated)
- Test: `v2/tests/test_query_correct_typo.py`

**Interfaces:**
- Produces: `closed_lexicon_fix(token: str, vocab: set[str]) -> str | None` — returns the UNIQUE vocab entry within Damerau-Levenshtein ≤2 of `token`, or `None` if no match OR two entries tie (ambiguous → leave as-is). Never invents; only maps INTO `vocab`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_typo.py
from v2.core.retrieval.query_correct import closed_lexicon_fix

VOCAB = {"student", "citations", "tuition", "computer science", "mathematics"}

def test_maps_into_vocab():
    assert closed_lexicon_fix("stdent", VOCAB) == "student"
    assert closed_lexicon_fix("citatns", VOCAB) == "citations"
    assert closed_lexicon_fix("tuishon", VOCAB) == "tuition"

def test_no_match_returns_none():
    assert closed_lexicon_fix("banana", VOCAB) is None   # >2 from everything

def test_exact_is_noop_none():
    assert closed_lexicon_fix("student", VOCAB) is None   # already in vocab, nothing to fix

def test_ambiguous_returns_none():
    # equidistant to two entries -> refuse (honest-partial)
    assert closed_lexicon_fix("statistics", {"statics", "statistcs"}) is None
```

- [ ] **Step 2: Run test to verify it fails.** `python -m pytest v2/tests/test_query_correct_typo.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `closed_lexicon_fix`** in `query_correct.py` (Damerau-Levenshtein ≤2, unique-winner-or-None):

```python
def _dl(a: str, b: str) -> int:
    # Damerau-Levenshtein (optimal string alignment)
    la, lb = len(a), len(b)
    d = [[0]*(lb+1) for _ in range(la+1)]
    for i in range(la+1): d[i][0] = i
    for j in range(lb+1): d[0][j] = j
    for i in range(1, la+1):
        for j in range(1, lb+1):
            cost = 0 if a[i-1] == b[j-1] else 1
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+cost)
            if i > 1 and j > 1 and a[i-1]==b[j-2] and a[i-2]==b[j-1]:
                d[i][j] = min(d[i][j], d[i-2][j-2]+1)
    return d[la][lb]

def closed_lexicon_fix(token: str, vocab: set[str]) -> str | None:
    t = token.lower()
    if t in vocab:
        return None
    best, best_d = None, 3
    for v in vocab:
        dist = _dl(t, v.lower())
        if dist < best_d:
            best, best_d = v, dist
        elif dist == best_d:
            best = None   # tie -> ambiguous, refuse
    return best
```

- [ ] **Step 4: Run test to verify it passes.** `python -m pytest v2/tests/test_query_correct_typo.py -v` — Expected: PASS.

- [ ] **Step 5: Apply in the router (gated).** In `route()`, before role/org/metric matching (gated on `query_correct.enabled()`), normalize a non-matching role/metric token by `closed_lexicon_fix` against the CLOSED vocab (`set(_ROLE_VOCAB)`, metric aliases from `profile_fields`, org names/aliases). Keep it token-scoped — never touch person names. Add an integration test: `route(conn, "who are the graduate stdent association officers")` → `officers_in_org` for GSA.

- [ ] **Step 6: Run tests + router gold.** `python -m pytest v2/tests/test_query_correct_typo.py v2/tests/ -k "router or gold" -v` — Expected: PASS, no new failures.

- [ ] **Step 7: Commit.** `git add v2/core/retrieval/query_correct.py v2/core/retrieval/router.py v2/tests/test_query_correct_typo.py && git commit -m "feat(query-correct): closed-lexicon edit<=2 typo tolerance for role/org/metric tokens"`

---

### Task 7: Eval breadth + gold no-regression + live smoke (evidence-before-claim)

**Files:**
- Modify: `eval/questions.txt` (add the structured-probe wins)
- Test/verify: `bash scripts/eval.sh`, `bash scripts/ask.sh`

**Interfaces:** none (verification task).

- [ ] **Step 1: Add the structured wins to `eval/questions.txt`** under a `# query-correction C+A` header: `who run cs`, `boss of cs`, `who president cs`, `boss of ywcc`, `top cited prof in computer sci`, `women in cs officers who`, `graduate student association officers who`, `most published prof in mathematics`. Plus the GUARD cases: `registrar office hours`, `who runs GSA`.

- [ ] **Step 2: Live smoke with the flag ON.** `QUERY_CORRECT_ENABLED=1 bash scripts/ask.sh "who run cs" --answer` — Expected: the CS chair from the KG, not a web page. Repeat for `boss of ywcc` (dean) and `top cited prof in computer sci` (metric ranking). Record the outputs.

- [ ] **Step 3: GSA-equal + guard smoke.** `QUERY_CORRECT_ENABLED=1 bash scripts/ask.sh "registrar office hours"` (must stay office, not a person) and `"who runs GSA"` (gsa officers/president, not biased). Record.

- [ ] **Step 4: No-regression gate.** `bash scripts/eval.sh` with the flag ON vs a baseline run with it OFF — Expected: coverage/accuracy not worse; the new structured Qs now answered. Record the before/after.

- [ ] **Step 5: Structured debt re-measure (spec §14.6).** Re-run the structured-arm probe (`python3 scratchpad/qc_structured_probe.py`) confirming the wins route; note the SURFACED conversion + a correctness spot-check of ~8 converted answers.

- [ ] **Step 6: Commit.** `git add eval/questions.txt && git commit -m "test(query-correct): add C+A structured wins + guard cases to eval; live smoke recorded"`

---

## Self-Review notes

- **Spec coverage:** G-A (Task 2/3) · G-C1r leader rule (Task 4/5) · G-C2r synonyms (folded into the leader mapping, Task 4) · G-C3r typo tolerance (Task 6) · flag/gating (Task 1, every wiring task) · GSA-equal + guards (Task 5 tests, Task 7 smoke) · eval/no-regression (Task 7). G6 LLM path is explicitly NOT built.
- **Deferred (logged, not built):** metric-by-research-area skill (`machine learning prof h index?`), unresolved-club data gaps, prose corpus-debt (fresh prose-recall track).
- **Marginal-value check (2026-06-29 caveat):** `_find_org` already resolves org acronyms natively, so the dictionary's routing lift is mostly the METRIC words (`prof`/`sci`) + the RAG leg, not org acronyms. Task 7 Step 4 must confirm the dictionary is net-positive; if any augmented string MISROUTES, apply spec O1b (split router-form vs retriever-form) — deferred until measured.
- **Flag gating:** the leader rule + typo tolerance read `query_correct.enabled()` at call time so a `.env` flip + restart flips them without reimport; OFF = router byte-identical to today.
