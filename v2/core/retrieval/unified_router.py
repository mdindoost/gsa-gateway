"""Kavosh v2.1 UnifiedRouter (Phase 1b).

ONE classify-then-resolve router replacing the four hand-maintained mechanisms. Layers run in
order in `decide()`: (0) deterministic COMMAND layer, (1) deterministic FAST-PATH (zero-encode),
(2) masked coarse-family CLASSIFIER (one router-prefixed nomic encode), (3) deterministic RESOLVER
(today's router.route() + its negative + terminal-skill guards), (4) CLARIFY / RAG outcomes / live.
The classifier picks the FAMILY only; the SQL skill stays deterministic. (Inverse-FN guard DEFERRED.)
"""
from __future__ import annotations
from dataclasses import dataclass, field

from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
)

_COMMAND_INTENTS = {
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
}


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
    def __init__(self, db_path, classifier, intent_detector):
        self.db_path = db_path                 # per-call short-lived connection (no shared conn)
        self.classifier = classifier
        self.intent_detector = intent_detector

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
