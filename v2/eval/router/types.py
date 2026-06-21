from __future__ import annotations
from dataclasses import dataclass, field


class Family:
    KG = "KG"; RAG = "RAG"; LIVE = "LIVE"; CLARIFY = "CLARIFY"; COMMAND = "COMMAND"
    ALL = (KG, RAG, LIVE, CLARIFY, COMMAND)


@dataclass
class RoutePrediction:
    family: str
    skill: str | None = None
    source: str | None = None        # RAG outcome: "food" | "event" | "general"
    slots: dict = field(default_factory=dict)
    score: float | None = None
    margin: float | None = None


@dataclass
class LabeledExample:
    id: str
    query: str
    family: str
    skill: str | None = None
    source: str | None = None
    slots: dict = field(default_factory=dict)
    group: str | None = None         # paraphrase-group id for disjoint split
