"""Answer-gate — the hybrid two-gate confidence design (spec §13.6).

Two complementary gates protect the small generator from confidently answering what it cannot know:

  Gate 1 — DETERMINISTIC, pre-retrieval INTENT cues. Catches what is knowable from the question
           ALONE: personal/account state ("what is my balance"), do-a-task ("write my essay"),
           other-institution ("tuition at Rutgers"), live+personal-referent. A hit => deflect
           IMMEDIATELY and skip fallback (no public page states a per-user balance). High precision,
           zero LLM cost. (spec fold #5/#6)

  Gate 2 — LLM ANSWERABILITY check, post-retrieval, evidence-first GRADED: require a verbatim
           supporting quote BEFORE a label {FULLY_SUPPORTED, PARTIALLY_SUPPORTED, NOT_IN_CONTEXT}.
           Run ONLY in the ambiguous ce_score band (gate-the-gate, spec fold #8) to avoid a serial
           8B call on every confident query. NOT_IN_CONTEXT is NEVER terminal — it routes to
           fallback (deep-fallback -> live -> deflect-only-if-all-miss, spec fold #5).

This module is the pure logic (deterministic Gate 1 + Gate 2 prompt/parse/decision). The actual LLM
call + retrieval live in the caller (scripts/eval_gate_shadow.py for measurement; later UnifiedRouter
for production). Build = SHADOW (read-only) first; DO NOT cut over without the owner gate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────── Gate 1 (deterministic intent)
@dataclass
class Gate1Verdict:
    deflect: bool
    cue: str | None = None       # "personal" | "task" | "other_institution" | "live" | None
    matched: str | None = None   # the matched phrase, for explainability


# Process / requirement / guidance frames — these are ANSWERABLE, never a private-record query.
# Checked FIRST so a possessive ("my transcript") in a how-to question does not false-fire (fold #5).
_GUIDANCE = re.compile(
    r"^\s*(how (do|can|should) i\b|how to\b|where do i\b|what do i need\b|"
    r"what are my (options|responsibilities|rights)\b|what(?:'s| is) the process\b)"
)

# Personal/account STATUS-or-VALUE query about the user's OWN record.
# Self-sufficient high-precision frames (fire alone):
_PERSONAL_SPECIFIC = [
    re.compile(r"\bhow much do i owe\b"),
    re.compile(r"\bwhen (will|is) my\b"),
    re.compile(r"\bhow many credits have i (completed|earned)\b"),
    re.compile(r"\bwhat grade did i (get|receive)\b"),
    # state-listing frame ("what holds/charges ARE on my account") — NOT incidental "my account"
    # mentions in a policy question ("why is there a hold on my account", caught in TDD held-out).
    re.compile(r"\bwhat\b.{0,20}\b(holds?|charges?|balance|fees?)\b.{0,15}\bon my\b"),
]
# "what is my X" / status-verb-start — fire ONLY with a personal-record noun (fold #5: never bare "my").
# Status verbs only (has/have/is/are/did/was/were) — NOT do/does ("does my GPA matter" is policy, I3).
_WHATIS_MY = re.compile(r"\bwhat(?:'s| is) my\b")
_PERSONAL_GENERIC = re.compile(r"^(has|have|is|are|did|was|were)\b.*\bmy\b")
# Conservative — clearly-private records only. Broad nouns (credits/grades/application/account/status)
# were removed because they false-fire on policy questions ("do my credits transfer", review I3).
_RECORD_NOUN = re.compile(
    r"\b(balance|holds?|refund|transcript|gpa|assistantship|financial aid|i-?20|visa|tuition refund)\b"
)

# do-a-task verbs governing a personal deliverable. The verb must sit NEAR the personal object so a
# bare interrogative "do I need …" never false-fires (caught in TDD); "do" is allowed only as "do my".
_TASK = re.compile(r"\b(write|draft|compose|fill out|prepare)\b.{0,40}\b(my|for me)\b|\bdo (my|the)\b.{0,40}\b(homework|assignment|essay|paper)\b")

# Other-institution names (not NJIT scope). Exempt "transfer ... from <school> ... to njit".
_OTHER_SCHOOL = re.compile(
    r"\b(rutgers|columbia|stevens|princeton|montclair|nyu|new york university|harvard|mit|"
    r"stanford|yale|seton hall|kean|rowan|tcnj|drexel|penn state)\b"
)

# Live cue — two ways to fire (events/food "today" carve-out kept, fold #5):
#  (1) time-cue + personal referent ("my appointment today");
#  (2) live-STATE word + now-word ("is the gym open right now") — review I6, no possessive needed.
_TIMECUE = re.compile(r"\b(today|tonight|right now|currently|at the moment|this minute)\b")
_POSSESS = re.compile(r"\bmy\b")
_LIVE_STATE_WORD = re.compile(r"\b(open|available|free|cancell?ed|busy|wait time|the line|seats? left|spots? left)\b")
_NOW_WORD = re.compile(r"\b(right now|now|today|tonight|currently|current|at the moment|this minute)\b")
_EVENT_FOOD = re.compile(r"\b(events?|menu|dining)\b")  # carve-out: these are answerable from the corpus


def gate1_intent(question: str) -> Gate1Verdict:
    """Deterministic intent gate. Returns deflect=True (with the cue family) iff a hard cue fires."""
    q = (question or "").strip().lower()
    if not q:
        return Gate1Verdict(False)

    guidance = bool(_GUIDANCE.match(q))

    # personal/account — skip when the question is a process/requirement frame
    if not guidance:
        for pat in _PERSONAL_SPECIFIC:
            m = pat.search(q)
            if m:
                return Gate1Verdict(True, "personal", m.group(0))
        if (_WHATIS_MY.search(q) or _PERSONAL_GENERIC.search(q)) and _RECORD_NOUN.search(q):
            return Gate1Verdict(True, "personal", _RECORD_NOUN.search(q).group(0))

    # do-a-task — skip guidance ("how do I write …")
    if not guidance:
        m = _TASK.search(q)
        if m:
            return Gate1Verdict(True, "task", m.group(0))

    # other-institution — exempt transferring INTO NJIT
    m = _OTHER_SCHOOL.search(q)
    if m and not ("transfer" in q and "njit" in q):
        return Gate1Verdict(True, "other_institution", m.group(0))

    # live — (1) time-cue + personal referent, or (2) live-state + now-word (no events/food)
    if _TIMECUE.search(q) and _POSSESS.search(q):
        return Gate1Verdict(True, "live", _TIMECUE.search(q).group(0))
    if _LIVE_STATE_WORD.search(q) and _NOW_WORD.search(q) and not _EVENT_FOOD.search(q):
        return Gate1Verdict(True, "live", _LIVE_STATE_WORD.search(q).group(0))

    return Gate1Verdict(False)


# ──────────────────────────────────────────────────────────────────── Gate 2 (LLM answerability)
_GATE2_SYSTEM = (
    "You are a strict grounding checker. Decide whether the CONTEXT contains a specific answer to the "
    "QUESTION. First copy one or more verbatim supporting quotes from the context that directly answer "
    "it; if no sentence answers it, leave the quote empty. A topic merely being mentioned is NOT "
    "support. Then assign a label: FULLY_SUPPORTED (the answer is stated), PARTIALLY_SUPPORTED (some "
    "but not all of it), or NOT_IN_CONTEXT (the specific answer is absent). Respond with ONLY a JSON "
    'object: {"supporting_quote": "...", "label": "...", "missing_piece": "..."}'
)


def gate2_prompt(question: str, context: list[str]) -> tuple[str, str]:
    """Build the (system, user) prompt for the evidence-first graded answerability check."""
    ctx = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(context)) or "(no context retrieved)"
    user = f"QUESTION: {question}\n\nCONTEXT:\n{ctx}\n\nJSON verdict:"
    return _GATE2_SYSTEM, user


@dataclass
class Gate2Verdict:
    label: str          # FULLY_SUPPORTED | PARTIALLY_SUPPORTED | NOT_IN_CONTEXT
    quote: str = ""
    missing: str = ""
    parsed: bool = True  # False => answer-biased default (model produced no valid JSON), review I2


_VALID_LABELS = {"FULLY_SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_IN_CONTEXT"}

# specific-fact-shaped questions: high retrieval relevance does NOT imply the datum is present, so
# these are answerability-checked even at high ce (review B2 — relevance != answerability for facts).
_FACT_SHAPED = re.compile(
    r"\b(how many|how much|how long|what percentage|pass rate|acceptance rate|graduation rate|"
    r"(number|count|total|average|amount|rate|percentage) of|compare(d)? to|how does .* compare)\b"
)


def is_fact_shaped(question: str) -> bool:
    return bool(_FACT_SHAPED.search((question or "").lower()))


def parse_gate2(raw: str) -> Gate2Verdict:
    """Parse the model's JSON verdict. Answer-biased: any parse failure => FULLY_SUPPORTED (parsed=False)
    so a grounding-checker malfunction NEVER withholds a real answer (never-withhold hard line)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            label = str(d.get("label", "")).strip().upper()
            if label in _VALID_LABELS:
                return Gate2Verdict(label, str(d.get("supporting_quote", "") or ""),
                                    str(d.get("missing_piece", "") or ""), parsed=True)
        except (ValueError, TypeError):
            pass
    return Gate2Verdict("FULLY_SUPPORTED", parsed=False)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def quote_grounded(quote: str, context, min_overlap: float = 0.6) -> bool:
    """True iff the model's supporting quote actually appears (fuzzily) in the context — guards against
    a hallucinated citation surviving as FULLY_SUPPORTED (review I5; Self-RAG ISSUP / RAGAS faithfulness).
    Uses longest-common-substring ratio, not exact match (tolerant of partials/multi-hop)."""
    import difflib
    q = _norm(quote)
    if not q:
        return False
    c = _norm("\n".join(context) if isinstance(context, (list, tuple)) else context)
    if not c:
        return False
    match = difflib.SequenceMatcher(None, q, c).find_longest_match(0, len(q), 0, len(c))
    return match.size / len(q) >= min_overlap


def verify_support(verdict: Gate2Verdict, context) -> Gate2Verdict:
    """Downgrade a PARSED FULLY/PARTIALLY_SUPPORTED verdict to NOT_IN_CONTEXT when its quote is not
    grounded in the context. Leaves the answer-biased (parsed=False) default untouched (never-withhold)."""
    if verdict.parsed and verdict.label in ("FULLY_SUPPORTED", "PARTIALLY_SUPPORTED") \
            and not quote_grounded(verdict.quote, context):
        return Gate2Verdict("NOT_IN_CONTEXT", verdict.quote, verdict.missing, parsed=True)
    return verdict


# ──────────────────────────────────────────────────────────────────── decision (gate-the-gate + ordering)
@dataclass
class GateDecision:
    outcome: str          # "deflect" | "answer" | "fallback"
    run_gate2: bool        # whether Gate 2 should be (or was) invoked
    skip_fallback: bool    # Gate-1 deflects skip fallback entirely (fold #5)


def gate_decision(gate1_cue: str | None, ce_score: float | None, gate2_label: str | None,
                  band: float, fact_shaped: bool = False) -> GateDecision:
    """Combine Gate 1 + gate-the-gate ce band + Gate 2 into a shadow outcome.

    gate2_label=None means Gate 2 has not been evaluated yet: the returned run_gate2 tells the caller
    whether it must run it. Gate-the-gate skips Gate 2 when retrieval is confident (ce>=band) — EXCEPT
    for fact_shaped questions, where high relevance does not imply the datum is present (review B2).
    """
    if gate1_cue:
        return GateDecision("deflect", run_gate2=False, skip_fallback=True)

    run_gate2 = fact_shaped or ce_score is None or ce_score < band
    if not run_gate2:
        return GateDecision("answer", run_gate2=False, skip_fallback=False)

    if gate2_label is None:
        return GateDecision("answer", run_gate2=True, skip_fallback=False)  # caller must run Gate 2
    if gate2_label == "NOT_IN_CONTEXT":
        return GateDecision("fallback", run_gate2=True, skip_fallback=False)  # never terminal
    return GateDecision("answer", run_gate2=True, skip_fallback=False)
