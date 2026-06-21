from __future__ import annotations
from dataclasses import dataclass, field


class Family:
    KG = "KG"; RAG = "RAG"; LIVE = "LIVE"; CLARIFY = "CLARIFY"; COMMAND = "COMMAND"
    OTHER = "OTHER"   # non-routable: social/meta, out-of-scope, context-dependent follow-ups
    ALL = (KG, RAG, LIVE, CLARIFY, COMMAND, OTHER)


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
    # --- labeling-protocol provenance (Workstream B) ---
    provenance: str | None = None    # "real" (harvested) | "seed" (synthetic, train-only)
    split: str | None = None         # "train" | "test" (gold) | "hardneg" (boundary suite)
    annotator: str | None = None     # who assigned the gold label
    proposed_family: str | None = None  # what the LLM proposed (to audit anchoring/edit-rate)
    confirmed: bool | None = None     # human confirmed/edited the proposal
