"""Kavosh v2.1 UnifiedRouter (Phase 1b).

ONE classify-then-resolve router replacing the four hand-maintained mechanisms. Layers run in
order in `decide()`: (0) deterministic COMMAND layer, (1) deterministic FAST-PATH (zero-encode),
(2) masked coarse-family CLASSIFIER (one router-prefixed nomic encode), (3) deterministic RESOLVER
(today's router.route() + its negative + terminal-skill guards), (4) CLARIFY / RAG outcomes / live.
The classifier picks the FAMILY only; the SQL skill stays deterministic. (Inverse-FN guard DEFERRED.)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
)

_COMMAND_INTENTS = {
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
}

# High-precision structured cues (mirror router.py's org-anchored structured cues). A match runs
# the deterministic resolver directly with ZERO classifier encode — common KG intents stay at
# zero added latency (spec §8). On a miss we fall through to encode + classify.
_FASTPATH_CUE = re.compile(
    r"\b(faculty|professors?|officers?|e-?board|department|departments|"
    r"who teaches|teaches in|people (?:in|at|of)|staff (?:of|at|in)|"
    r"chair|dean|director|provost|citations?|cited|h-?index|i10)\b", re.I)


@dataclass
class RouteDecision:
    family: str
    skill: str | None = None
    args: dict = field(default_factory=dict)
    source: str | None = None
    command_intent: str | None = None
    score: float | None = None
    margin: float | None = None


class UnifiedRouter:
    def __init__(self, db_path, classifier, intent_detector, generate_json=None, tau=None):
        self.db_path = db_path                 # per-call short-lived connection (no shared conn)
        self.classifier = classifier
        self.intent_detector = intent_detector
        # Slot-extraction fallback (Workstream 1). `generate_json(system, prompt, schema)->dict|None`
        # is the SYNC constrained-JSON call (None disables the fallback → legacy None⇒RAG behavior).
        self.generate_json = generate_json
        # LLM self-confidence is a SECONDARY gate only (§8): the resolver + family classifier are the
        # PRIMARY false-positive guard. Default 0.0 (resolver-primary); settings-tunable/calibratable.
        self.tau = 0.0 if tau is None else tau

    def _route(self, message):
        """Run the deterministic resolver on a short-lived connection (FTS+SQL only, no vec0)."""
        import sqlite3
        from v2.core.retrieval import router as srouter
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            return srouter.route(conn, message)
        finally:
            conn.close()

    def command_layer(self, message: str) -> RouteDecision | None:
        intent, _score = self.intent_detector.detect(message)
        if intent in _COMMAND_INTENTS:
            return RouteDecision(family="COMMAND", command_intent=intent)
        return None

    def resolve_kg(self, message: str) -> "RouteDecision":
        rt = self._route(message)              # short-lived conn; carries all negative guards
        if rt is not None:
            return RouteDecision(family="KG", skill=rt.skill, args=dict(rt.args))
        # ── Fallback: regex route() found nothing → constrained-JSON slot extraction (Workstream 1).
        # Only runs when the classifier already said KG and a generator is wired. Fail-safe: any
        # miss (none / low-confidence / unresolved slot) degrades to the unchanged RAG/general.
        if self.generate_json is None:
            return RouteDecision(family="RAG", source="general")
        try:
            from v2.core.retrieval.slot_extractor import extract_slots, resolve_and_validate
            ext = extract_slots(message, self.generate_json)
            if ext.skill == "none" or ext.confidence < self.tau:
                return RouteDecision(family="RAG", source="general")
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                resolved = resolve_and_validate(conn, ext.skill, ext.slots, message)
            finally:
                conn.close()
            if resolved is None:               # unresolved/hallucinated slot → never guess
                return RouteDecision(family="RAG", source="general")
            return RouteDecision(family="KG", skill=resolved.skill, args=dict(resolved.args))
        except Exception:                      # the fallback must NEVER break the answer path
            return RouteDecision(family="RAG", source="general")

    def _rag_outcome(self, message: str) -> str:
        from bot.services.intent_detector import INTENT_FOOD
        intent, _ = self.intent_detector.detect(message)
        if intent == INTENT_FOOD:
            return "food"
        # Word-boundary event cue (so "in the event of" / "eventually" don't fire). The "event"
        # label is an ADVISORY boost hint ONLY — E1 must NOT translate it into a source_type
        # item_types filter (that DISABLES BM25 → kills recall on sparse/acronym/award queries,
        # spec §4). Only "food" has a dedicated handler; "event"/"general" go through normal RAG
        # with the retriever's existing event_info boost. [RAG-review S2 / spec §4]
        import re as _re
        if _re.search(r"\b(events?|workshops?|seminars?|happening)\b", message.lower()):
            return "event"
        return "general"

    def fast_path(self, message: str) -> "RouteDecision | None":
        if not _FASTPATH_CUE.search(message):
            return None
        rt = self._route(message)              # short-lived conn (no classifier encode)
        if rt is None:
            return None
        return RouteDecision(family="KG", skill=rt.skill, args=dict(rt.args))

    def decide(self, message: str) -> "RouteDecision":
        cmd = self.command_layer(message)
        if cmd is not None:
            return cmd
        fp = self.fast_path(message)
        if fp is not None:
            return fp
        ranked = self.classifier.ranked(message)
        top = ranked[0][0] if ranked else "RAG"
        if top == "KG":
            return self.resolve_kg(message)        # the inverse-FN guard is REMOVED (Task C3 deferred)
        if top in ("RAG", "CLARIFY"):
            # Abstention is BUILT-but-OFF in Phase 1b, so a classifier CLARIFY must degrade to RAG
            # (identical to today, never a rephrase prompt that reads worse). When abstention is
            # wired later, CLARIFY becomes a deliberate outcome again. [flip-gate review 2026-06-22]
            return RouteDecision(family="RAG", source=self._rag_outcome(message),
                                 score=ranked[0][1])
        if top == "LIVE":
            return RouteDecision(family="LIVE", score=ranked[0][1])
        # OTHER / a COMMAND family from the classifier
        return RouteDecision(family=top, score=(ranked[0][1] if ranked else None))
