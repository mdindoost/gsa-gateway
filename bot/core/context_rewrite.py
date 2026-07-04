"""Contextual query rewrite — resolve conversation follow-ups into standalone queries.

Backlog #2 / the #1 evidenced 👎 bug: conversation history reaches answer GENERATION but never
RETRIEVAL/ROUTING, so a follow-up like "what is his position" retrieves wrong chunks. This module
provides a deterministic referential GATE (no LLM), a deterministic entity-membership VERIFICATION
(the anti-fab guard against a confident *wrong* rewrite), and an orchestrator that wraps the LLM
rewrite with both. The LLM call itself lives in ollama_client.rewrite_with_context.

Spec: docs/superpowers/specs/2026-06-22-contextual-query-rewrite-design.md
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Unit 1: the deterministic referential gate ────────────────────────────────
# Bare personal pronouns (referential, no antecedent in the message).
_PRONOUNS = re.compile(r"\b(his|her|hers|him|its|their|them|they|he|she)\b", re.I)
# Demonstratives used PRONOMINALLY (sentence-final), not as determiners ("those professors").
_DEMONSTRATIVE = re.compile(r"\b(that|those|this|these)\b\s*[?.!]*\s*$", re.I)
# Elliptical follow-up openers.
_OPENERS = re.compile(r"^\s*(what about|how about|and\b|why not|what else|the official one|the other one)", re.I)


def is_follow_up(message: str) -> bool:
    """True iff the message carries a REFERENTIAL signal (bare pronoun / elliptical opener) — NOT
    mere shortness (a complete terse query like "office hours" must not fire)."""
    m = (message or "").strip()
    if not m:
        return False
    return bool(_OPENERS.search(m) or _PRONOUNS.search(m) or _DEMONSTRATIVE.search(m))


# ── Unit 2: deterministic entity-membership verification (the load-bearing guard) ──
_STOP = {
    "what", "whats", "is", "are", "am", "be", "the", "a", "an", "of", "for", "in", "on", "to",
    "do", "does", "did", "you", "your", "me", "my", "i", "can", "could", "how", "who", "whos",
    "when", "where", "why", "which", "and", "about", "his", "her", "hers", "him", "its", "it",
    "their", "them", "they", "he", "she", "that", "those", "this", "these", "with", "at", "by",
    "from", "as", "or", "not", "didnt", "dont", "please", "pls",
}


def _content_words(s: str) -> set[str]:
    toks = (t.strip("'.-") for t in re.findall(r"[A-Za-z0-9'./-]+", (s or "").lower()))
    return {t for t in toks if t and t not in _STOP}


def _added_entities(s: str) -> list[str]:
    """Proper-noun-ish tokens (capitalized, not sentence-initial), possessive-normalized + lowercased."""
    toks = re.findall(r"[A-Za-z][A-Za-z'.-]*", s or "")
    out = []
    for i, t in enumerate(toks):
        if i == 0 and t.lower() in _STOP:   # skip a sentence-initial stopword/question word ONLY
            continue                        # (a real entity at position 0 must still be checked) [H2]
        if t[0].isupper():
            e = re.sub(r"'s$", "", t, flags=re.I).strip("'.-").lower()
            if e:
                out.append(e)
    return out


# ── A3: antecedent-ambiguity — the picked-name-is-a-list-item backstop ────────
# Layer 2 of the antecedent guard (spec 2026-07-04). Covers rosters layer 1 can't tag (a RAG/prose
# answer that names several people): if the LLM's rewrite added exactly ONE full name and that name
# is a member of a ≥3-name list-chain in history with NO standalone occurrence, the pick was plucked
# from a list → passthrough. Operates on ORIGINAL-CASE history (needs capitalization).

# A maximal run of consecutive capitalized words ("Bryan Pfister", "Computer Vision", "Distinguished
# Professor"). A comma/'and'/';' between runs breaks them into separate runs.
_CAP_RUN = re.compile(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)*")
# A list separator between two capitalized runs (comma / 'and' / '&' / semicolon / newline-bullet).
_LIST_SEP = re.compile(r"\s*(?:,|;|&|,?\s*and)\s*(?:[-•*]\s*)?$", re.I)


def _norm_run(s: str) -> str:
    """Normalize a capitalized run for comparison: strip possessive + edge punct, collapse space, lower."""
    s = re.sub(r"'s$", "", (s or "").strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" .,;'-").lower()


def _classify_runs(history: str) -> tuple[set[str], set[str]]:
    """Split history's capitalized runs into (list_members, standalone) by normalized name. A run is
    a LIST-MEMBER iff it belongs to a maximal chain of ≥3 runs linked only by list separators — so a
    2-run `Name, Title` appositive is NOT a list. A name can land in BOTH sets (list in one place,
    standalone in another): that's how a later standalone mention rescues an earlier roster pick."""
    runs = list(_CAP_RUN.finditer(history or ""))
    n = len(runs)
    linked = [False] * n                          # linked[i] == run i is list-joined to run i+1
    for i in range(n - 1):
        gap = (history or "")[runs[i].end():runs[i + 1].start()]
        if _LIST_SEP.fullmatch(gap):
            linked[i] = True
    member, standalone = set(), set()
    i = 0
    while i < n:
        j = i
        while j < n - 1 and linked[j]:
            j += 1
        group = [_norm_run(runs[k].group()) for k in range(i, j + 1)]
        (member if (j - i + 1) >= 3 else standalone).update(g for g in group if g and g != "i")
        i = j + 1
    return member, standalone


def _added_runs(original: str, resolved: str) -> list[str]:
    """Normalized capitalized runs the rewrite ADDED. A run is NOT 'added' if every token in it is a
    stopword or already appears (any case) in the original — so sentence-initial casing ("What is
    Bryan Pfister's h-index?") and a name the user already typed don't inflate the count (mirrors the
    [H2] sentence-initial skip in _added_entities). A bare 'I' is never a name."""
    orig_words = set(re.findall(r"[a-z0-9'.-]+", (original or "").lower()))
    out = []
    for r in _CAP_RUN.finditer(resolved or ""):
        nm = _norm_run(r.group())
        if not nm or nm == "i":                      # bare "I" is not a name
            continue
        toks = [t for t in nm.split() if t and t != "i"]
        if toks and all(t in _STOP or t in orig_words for t in toks):
            continue                                 # all-stopword / already-present run → not added
        out.append(nm)
    return out


def _is_roster_pick(original: str, resolved: str, history: str) -> bool:
    """True iff the rewrite added exactly ONE name that is a ≥3-chain list-member in history with no
    standalone occurrence — i.e. the LLM plucked one arbitrary name from a roster (the A3 pathology)."""
    added = _added_runs(original, resolved)
    if len(added) != 1:
        return False                     # added ≥2 = legitimate resolve-to-set; added 0 = nothing to judge
    member, standalone = _classify_runs(history)
    nm = added[0]
    return nm in member and nm not in standalone


def verify_rewrite(original: str, resolved: str, history: str, guard_enabled: bool = False) -> str:
    """Return `resolved` only if it is a SAFE rewrite of `original`; else passthrough `original`.

    Guards (deterministic, no LLM):
      - empty/unchanged → passthrough.
      - balloon (>3x length) → passthrough (intent change / runaway).
      - intent change: every content word of the original must survive in the resolved.
      - entity-membership (THE anti-fab guard): every proper-noun the rewrite ADDED must appear
        literally in the history; a hallucinated antecedent → discard → passthrough.
      - roster-pick backstop (A3, guard_enabled only): the added name is a ≥3-chain list-member with
        no standalone occurrence → an arbitrary pick from a roster → discard → passthrough.
    """
    original = (original or "").strip()
    resolved = (resolved or "").strip()
    if not resolved or resolved.lower() == original.lower():
        return original
    if len(resolved) > 3 * max(len(original), 1):
        return original
    if not _content_words(original).issubset(_content_words(resolved)):
        return original                  # original's question content didn't survive → intent change
    hist = (history or "").lower()
    orig_entities = set(_added_entities(original))
    for e in _added_entities(resolved):
        if e in orig_entities:
            continue
        if e not in hist:
            logger.warning("context_rewrite: discarded rewrite with unsupported entity %r "
                           "(orig=%r resolved=%r)", e, original, resolved)
            return original              # hallucinated antecedent → passthrough
    if guard_enabled and _is_roster_pick(original, resolved, history):
        logger.info("context_rewrite[a3]: discarded roster-antecedent pick (orig=%r resolved=%r)",
                    original, resolved)
        return original                  # 1-of-N plucked from a roster → passthrough
    return resolved


# ── A3: antecedent-ambiguity — the pre-LLM clarify gate (layer 1) ─────────────
# Bare SINGULAR PERSONAL pronouns only (his/her/hers/him/he/she). Plural (their/them/they) over a
# roster has a valid antecedent (the set) → not gated. 'its' is not personal → excluded.
_SINGULAR_PERSONAL = re.compile(r"\b(his|her|hers|him|he|she)\b", re.I)


def _preceding_person_turn_names(history_turns) -> list[str]:
    """The person_names tagged on the IMMEDIATELY-PRECEDING assistant turn (the anaphora scope).
    Adjacency-scoped, NOT most-recent-tagged: tags exist only on structured turns, so a stale roster
    two turns back must not shadow an untagged RAG answer that just named the referent."""
    for t in reversed(history_turns or []):
        if (t.get("role") if isinstance(t, dict) else None) == "assistant":
            return list(t.get("person_names") or [])
    return []


def ambiguity_clarify(message: str, history_turns) -> str | None:
    """Return a CLARIFY prompt iff the message is a bare singular-personal-pronoun follow-up AND the
    immediately-preceding assistant turn named ≥2 people (an unresolvable antecedent). Else None."""
    if not _SINGULAR_PERSONAL.search(message or ""):
        return None
    names = [n for n in _preceding_person_turn_names(history_turns) if n]
    if len(names) < 2:
        return None
    shown = names[:5]
    tail = ", …" if len(names) > 5 else ""
    return (f"You mentioned several people — which one did you mean? "
            f"{', '.join(shown)}{tail} (or give the full name).")


def _log_unverified_lowercase(original: str, resolved: str, history: str) -> None:
    """Measurement-only (A3 deferred lowercase-bypass fix): log lowercase content words the rewrite
    ADDED that aren't in history. Pure signal, no behavior change — informs whether the isupper()
    bypass ever bites in real traffic before we engineer for it."""
    try:
        hist = (history or "").lower()
        caps = set(_added_entities(resolved))    # already-verified capitalized tokens
        sus = [w for w in (_content_words(resolved) - _content_words(original))
               if w not in hist and w not in caps]
        if sus:
            logger.info("context_rewrite[a3-measure]: added lowercase words not in history %r "
                        "(resolved=%r)", sus, resolved)
    except Exception:  # noqa: BLE001 - measurement must never affect the path
        pass


# ── Unit 3: the rewrite prompt (pure; ollama_client does the HTTP) ────────────
REWRITE_SYSTEM = (
    "You rewrite a student's follow-up message into a single STANDALONE question, using ONLY the "
    "conversation history for context. Rules: (1) Resolve pronouns and ellipsis using ONLY names, "
    "people, departments, or topics that appear in the history — never invent. (2) Do NOT change the "
    "TYPE of question (resolve references only; keep it the same ask). (3) If more than one entity "
    "could be the referent, or you cannot resolve it from the history, return the message UNCHANGED. "
    "Output ONLY the standalone question, nothing else."
)


def build_rewrite_prompt(history: str, message: str) -> tuple[str, str]:
    """Return (system, user) prompts for the contextual rewrite. Pure — unit-testable."""
    user = (
        "Conversation history:\n"
        f"{history}\n\n"
        f"Follow-up message: {message}\n\n"
        "Standalone question:"
    )
    return REWRITE_SYSTEM, user


# ── Unit 4: orchestrator — gate → LLM rewrite → deterministic verify ──────────
def _format_history(turns) -> str:
    lines = []
    for t in (turns or []):
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{t.get('role', '')}: {content}")
    return "\n".join(lines)


@dataclass
class RewriteResult:
    """Result of resolve_query. `query` is what drives routing/retrieval; `clarify_text`, when set,
    is a deterministic 'which person did you mean?' response the caller returns instead of routing
    (A3 layer 1). Stable shape in BOTH flag states (clarify_text is just always None when off)."""
    query: str
    rewritten: bool
    clarify_text: str | None = None


async def resolve_query(message: str, history_turns, llm) -> RewriteResult:
    """Resolve a follow-up into a standalone query. Returns a RewriteResult.

    Passthrough the ORIGINAL (rewritten=False) on: gate-miss, no history, no llm, an LLM failure, or
    a verify that discards the rewrite. One LLM call only when the referential gate fires.

    A3 (guarded by ANTECEDENT_GUARD_ENABLED): before the LLM, a bare singular-pronoun follow-up whose
    immediately-preceding assistant turn named ≥2 people short-circuits to a CLARIFY (no LLM call);
    and verify_rewrite runs the roster-pick backstop."""
    import bot.config as botcfg
    message = message or ""
    if not is_follow_up(message) or not history_turns or llm is None:
        return RewriteResult(message, False)
    guard_on = botcfg.ANTECEDENT_GUARD_ENABLED
    if guard_on:                              # layer 1: pre-LLM ambiguity gate → clarify
        clarify = ambiguity_clarify(message, history_turns)
        if clarify is not None:
            return RewriteResult(message, False, clarify_text=clarify)
    history_text = _format_history(history_turns)
    try:
        resolved = await llm.rewrite_with_context(history_text, message)
    except Exception:  # noqa: BLE001 - never break the message path; passthrough
        logger.warning("context_rewrite: LLM rewrite failed; passthrough", exc_info=True)
        return RewriteResult(message, False)
    verified = verify_rewrite(message, resolved or "", history_text, guard_enabled=guard_on)
    rewritten = verified.strip().lower() != message.strip().lower()
    if rewritten:
        logger.info("context_rewrite: %r -> %r", message, verified)
        _log_unverified_lowercase(message, verified, history_text)
    return RewriteResult(verified, rewritten)
