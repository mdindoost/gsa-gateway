"""WS4 post-generation faithfulness / answerability gate (validated combined design).

The v2.5 answer-gate's over-answer guard was a pre-generation Gate-2 (LLM answerability) whose
verdict was post-checked by a BRITTLE longest-contiguous-substring `quote_grounded` — which broke on
markdown emphasis (`**30 days**`) and false-rejected correct answers (the Chrome-River bug), while
accidentally catching some fabrications by the same brittleness. The bake-off (docs spec
2026-07-02-ws4-…) showed the dominant fabrication mode is GROUNDED-BUT-IRRELEVANT paste (real passage
text that does not answer THIS question) — which no groundedness/faithfulness check can catch — and that
a whole-answer NLI/LLM faithfulness gate OVER-ABSTAINS on paraphrased grounded answers.

This module is the validated combined ANSWERABILITY gate, run POST-generation on the composed answer:

  1. subjective superlative ("best/easiest professor") .......... abstain (no objective answer)
  2. answer-type grounding for typed questions (count/rate/money/date):
       the answer must contain a value OF THE EXPECTED TYPE that is GROUNDED in the passages,
       else abstain — this catches grounded-but-irrelevant numeric pastes AND rescues Gate-2's
       false-negatives on real numeric answers (deterministic, no LLM, CPU-free).
  3. non-typed factual residual .............................. defer to a Gate-2 answerability verdict
       (supplied by the caller), post-checked by ROBUST markdown-normalized token-set grounding.

Pure logic — the Gate-2 LLM call + the self-abstain / Gate-1 checks live in the caller (message_handler),
mirroring answer_gate.py. Validated: false-answer 20%→10%, gate-attributable false-abstain 12%→0%.
"""
from __future__ import annotations

import re

from v2.core.people.profile_fields import match_metric

# ─────────────────────────────────────────────────────────────── normalization
_MD = re.compile(r"[*_#`]+")
_NONWORD = re.compile(r"[^a-z0-9 %$.]+")
_WS = re.compile(r"\s+")
_PCT_WS = re.compile(r"\s+%")  # senior #6: "85 %" -> "85%" so rate spacing never breaks grounding


def _norm(s: str) -> str:
    """Lowercase; strip markdown emphasis + punctuation (keep % $ . for typed values); collapse ws;
    glue a percent sign onto its number ("85 %" -> "85%")."""
    s = _WS.sub(" ", _NONWORD.sub(" ", _MD.sub(" ", (s or "").lower()))).strip()
    return _PCT_WS.sub("%", s)


# ─────────────────────────────────────────────────────────────── subjective superlative guard
_SUBJECTIVE = re.compile(
    r"\b(best|worst|easiest|hardest|toughest|greatest|favou?rite|"
    r"most (?:popular|prestigious|respected|difficult))\b"
    # RAG review #6: exclude answerable how-to senses ("best WAY/TIME to …", "best PRACTICES")
    r"(?!\s+(?:way|time|practices?|approach|method|option|choice|idea|moment)\b)",
    re.IGNORECASE,
)


def is_subjective(question: str) -> bool:
    """True when the question asks for a subjective superlative with no objective corpus answer."""
    return bool(_SUBJECTIVE.search(question or ""))


# ─────────────────────────────────────────────────────────────── explicit non-answer (self-decline)
# NARROW on purpose: only an explicit "I couldn't find / I don't have / not in the KB" admission counts
# as the model declining. It must NOT match a real answer that merely points to a source ("visit
# admissions.njit.edu", "for current hours see …") — the broad deflection detector over-triggered on
# those and, with temp>0 compose variance, non-deterministically abstained on good answers.
_NONANSWER = re.compile(
    r"\bi (?:don't|do not|was ?n't|were ?n't|could ?n't|cannot|can't)(?: able to)? (?:have|find|locate|see)\b"
    r"|\bi (?:don't|do not) have (?:that|specific|detailed|the exact|enough|any)\b"
    r"|\bnot (?:available|listed|specified|included|found|mentioned) in (?:the|our|my|gsa|this)\b"
    r"|\bunable to (?:find|locate|provide)\b",
    re.IGNORECASE,
)


def is_explicit_nonanswer(text: str) -> bool:
    """True only when the composed answer explicitly declines (no info) — NOT when it answers and
    happens to cite/point to a source. Used as the gate's self-abstain passthrough."""
    return bool(_NONANSWER.search(text or ""))


# ─────────────────────────────────────────────────────────────── expected answer type
_Q_RATE = re.compile(r"\b(pass rate|acceptance rate|graduation rate|percentage|what percent|how likely)\b", re.I)
_Q_COUNT = re.compile(r"\bhow many\b|\bnumber of\b", re.I)
# RAG review #3: "how much" only means MONEY when not "how much TIME/LONGER/NOTICE/effort/…"
_Q_MONEY = re.compile(
    r"\b(tuition|cost|fee|amount|budget|price)\b"
    r"|\bhow much\b(?!\s+(?:time|longer|long|notice|effort|coursework|weight|sooner|later)\b)",
    re.I)
_Q_DATE = re.compile(r"\b(deadline|due date|what date|when is the|when are the)\b", re.I)


def expected_answer_type(question: str) -> str | None:
    """Classify the datum type the question demands: 'count' | 'rate' | 'money' | 'date' | None.
    Order matters — rate before count ('pass rate' is not a 'how many')."""
    q = question or ""
    if _Q_RATE.search(q):
        return "rate"
    if _Q_COUNT.search(q):
        return "count"
    if _Q_MONEY.search(q):
        return "money"
    if _Q_DATE.search(q):
        return "date"
    return None


# ─────────────────────────────────────────────────────────────── typed-value extraction
_NUMWORD = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUMWORD.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
                 "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100})
# digit -> spelled-out word, for count digit<->word equivalence (RAG review #5)
_WORDNUM = {n: w for w, n in _NUMWORD.items()}

_RATE_V = re.compile(r"\d{1,3}(?:\.\d+)?\s?%|\b\d{1,3}\s?percent\b", re.I)
_MONEY_V = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*\s?dollars\b", re.I)
_MONEY_BARE = re.compile(r"\b\d{2,3}(?:,\d{3})+|\b\d{3,6}\b")
_DATE_V = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}|"
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?|\b(?:monday|tuesday|wednesday|thursday|friday)\b|\b\d{1,2}(?:st|nd|rd|th)\b|"
    # RAG review #4: deadlines expressed as relative durations ("within 30 days", "10 business days after")
    r"\bwithin\s+\d{1,3}\s+(?:business\s+)?(?:days?|weeks?|months?)\b|"
    r"\b\d{1,3}\s+(?:business\s+)?(?:days?|weeks?|months?)\s+(?:before|after|prior|of|from)\b",
    re.I)
# RAG review #5 / senior #1: extract multi-digit counts (comma-grouped or bare, up to 6 digits);
# years are excluded downstream in _typed_values (senior #2 — that guard is now reachable).
_COUNT_DIGIT = re.compile(r"\b\d[\d,]*\b")


def _typed_values(answer: str, atype: str) -> list[str]:
    """Extract candidate answer values of `atype` from the composed answer."""
    a = answer or ""
    if atype == "rate":
        return [_norm(m.group(0)) for m in _RATE_V.finditer(a)]
    if atype == "money":
        vals = [_norm(m.group(0)) for m in _MONEY_V.finditer(a)]
        for m in _MONEY_BARE.finditer(a):  # bare digit-runs, EXCLUDE 4-digit years (senior #3)
            raw = m.group(0).replace(",", "")
            if raw.isdigit() and not (1900 <= int(raw) <= 2099):
                vals.append(raw)
        return vals
    if atype == "date":
        return [_norm(m.group(0)) for m in _DATE_V.finditer(a)]
    # count: digits (EXCLUDE 4-digit years) + spelled-out small numbers
    vals: list[str] = []
    for m in _COUNT_DIGIT.finditer(a):
        raw = m.group(0).replace(",", "")
        if raw.isdigit() and not (1900 <= int(raw) <= 2099):
            vals.append(raw)
    for w in re.findall(r"[a-z]+", a.lower()):
        if w in _NUMWORD:
            vals.append(w)
    return [_norm(v) for v in vals]


def answer_has_grounded_type(answer: str, passages, atype: str) -> bool:
    """True iff the answer contains a value of `atype` that also appears in the passages (grounded)."""
    ctx = _norm("\n".join(passages) if isinstance(passages, (list, tuple)) else passages)
    ctx_tokens = set(ctx.split())
    # per-token digit content (word-boundary values) — NOT a boundary-free digit concatenation, so
    # "500" is not falsely grounded by an incidental "15003" (senior #4)
    ctx_numtokens = {re.sub(r"[^0-9]", "", t) for t in ctx_tokens}
    for v in _typed_values(answer, atype):
        if not v:
            continue
        if atype == "count":
            forms = {v}  # digit<->word equivalence (RAG review #5): "four" grounds "4" and vice versa
            if v.isdigit():
                w = _WORDNUM.get(int(v))
                if w:
                    forms.add(w)
            elif v in _NUMWORD:
                forms.add(str(_NUMWORD[v]))
            if forms & ctx_tokens:
                return True
        elif atype == "money":
            if ("$" in v or "dollar" in v) and v in ctx:  # literal "$900" / "900 dollars"
                return True
            digits = re.sub(r"[^0-9]", "", v)
            # bare amount must equal a WHOLE context value, not be an incidental substring (senior #4)
            if len(digits) >= 3 and digits in ctx_numtokens:
                return True
        else:  # rate, date — multi-token substrings
            if v in ctx:
                return True
    return False


# ─────────────────────────────────────────────────────────────── robust quote grounding
def robust_grounded(quote: str, passages, min_overlap: float = 0.7) -> bool:
    """Token-set overlap: fraction of the quote's tokens present in the context, order/format
    independent. Replaces the brittle longest-contiguous-substring test that markdown emphasis broke
    (the Chrome-River false-abstain). A hallucinated quote (tokens absent from context) still fails."""
    q = _norm(quote).split()
    if not q:
        return False
    ctx = set(_norm("\n".join(passages) if isinstance(passages, (list, tuple)) else passages).split())
    if not ctx:
        return False
    return sum(1 for t in q if t in ctx) / len(q) >= min_overlap


# ─────────────────────────────────────────────────────────── deterministic metric backstop (gate2 hardening)
def metric_query_without_grounded_metric(question: str, passages) -> bool:
    """True iff the QUESTION asks for a Scholar METRIC (citations / h-index / i10) but NONE of the
    retrieved PASSAGES contain that metric's terms — the KB prose holds no metric data, so a composed
    answer would mis-attribute a number or a person (the wrong-professor drift the tiny gate model
    leaks as FULLY_SUPPORTED). Metric questions belong to the structured metric skill; reaching prose
    RAG is already a routing miss, so abstaining here is correct. Deterministic, model-free, and it
    checks the PASSAGES not the answer (the answer's 'Cited Document:' attribution boilerplate contains
    the word 'cited' and would otherwise mask a metric-less context).

    Wired ONLY at the KB faithfulness gate, not the live-fallback relevance gate — live spans are
    verbatim njit.edu page text, which never carries Scholar metrics (Scholar data is manual-only), so
    a metric question can only be metric-grounded via the KB path anyway."""
    m = match_metric(question or "")
    if not m:
        return False
    _, metric = m
    hay = _norm(" \n ".join(passages) if isinstance(passages, (list, tuple)) else (passages or ""))
    # Normalize each alias through _norm too, so a hyphenated alias ("h-index") matches the normalized
    # passage text ("h index") without relying on the registry also carrying a redundant space-variant.
    return not any((na := _norm(a)) and re.search(r"\b" + re.escape(na) + r"\b", hay)
                   for a in metric.aliases)


# ─────────────────────────────────────────────────────────────── decision (two-phase)
_SUPPORTED = {"FULLY_SUPPORTED", "PARTIALLY_SUPPORTED"}


def assess_pre_gate2(question: str, answer: str, passages) -> tuple[str, str]:
    """Deterministic pre-Gate-2 assessment on a real composed answer (caller has already handled
    Gate-1 deflect + Granite self-abstain). Returns (outcome, reason):
      'abstain'  — subjective, or a typed question whose answer lacks a grounded value of the type
      'answer'   — a typed question whose answer HAS a grounded value of the expected type
      'gate2'    — non-typed factual: the caller must run the Gate-2 answerability check
    """
    if is_subjective(question):
        return "abstain", "subjective"
    atype = expected_answer_type(question)
    if atype:
        if answer_has_grounded_type(answer, passages, atype):
            return "answer", f"typed-grounded:{atype}"
        return "abstain", f"typed-ungrounded:{atype}"
    return "gate2", "nontyped-factual"


def decide_after_gate2(gate2_label: str, gate2_quote: str, passages, parsed: bool = True) -> tuple[str, str]:
    """Finalize the non-typed residual given the caller's Gate-2 verdict. A SUPPORTED verdict answers
    ONLY with a NON-EMPTY, robustly-grounded supporting quote; a NOT_IN_CONTEXT, a parse failure, or a
    quote-less / ungrounded quote abstains.

    NOTE — this DIVERGES from senior review #5 (which asked parse-fail / quote-less SUPPORTED to answer,
    to avoid false-abstain). The WS4 both-directions eval overruled it: parse-fail/empty-quote answering
    LEAKED a fabrication ("capital of France": Granite emitted an unparseable FULLY_SUPPORTED for an
    out-of-domain question) while NO should-answer question relied on it (0 measured false-abstain
    benefit). Gate-2 runs at temp 0.0, so a parse failure is DETERMINISTIC (out-of-domain garbage), not a
    transient glitch; and robust_grounded already TOLERATES paraphrase (token-set >=0.7), so the reviewer's
    "reworded quote" case still answers. False-answer is the priority metric → require real support.

    # Positive-span reframe (2026-07-08): PARTIALLY_SUPPORTED means "the question's PRIMARY ask is
    # answered though a secondary detail is missing" — so compound questions surface here instead of
    # dying as NOT_IN_CONTEXT. The abstain/answer wiring below is unchanged; only the LLM's label
    # criterion (in _GATE2_SYSTEM) moved.
    """
    if gate2_label not in _SUPPORTED:
        return "abstain", "gate2:not-in-context"
    if not parsed or not robust_grounded(gate2_quote, passages):
        return "abstain", "gate2:unsupported"
    return "answer", f"gate2:{gate2_label}"
