from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class OracleCitation:
    url: str
    title: str | None = None
    snippet: str | None = None

@dataclass
class OracleAnswer:
    question: str
    answer: str
    citations: list[OracleCitation] = field(default_factory=list)
    raw: dict | None = None

@dataclass
class Nugget:
    text: str
    vital: bool

@dataclass
class GuardVerdict:
    verdict: str          # 'supported' | 'unsupported' | 'we_are_authority'
    cited_url: str | None = None
    evidence_span: str | None = None

@dataclass
class PresenceEvidence:
    source_type: str      # 'node' | 'knowledge_item'
    row_or_node_id: str
    span: str
    probe: str            # 'kg_probe' | 'fts_probe' | 'embed_probe' | 'grep_probe'
    item_type: str | None = None   # knowledge_items.type, when applicable (e.g. 'publication')

@dataclass
class PresenceResult:
    present: bool                  # confident presence only: some span's P(entail) >= HI ('yes')
    probes_hit: list[str]
    evidence: list[PresenceEvidence]
    unsure_only: bool = False      # legacy field; under the new lean == low_conf (kept for old readers)
    low_conf: bool = False         # NOT present, but a span landed in [LO,HI) -> surfaced for adjudication
    max_score: float = 0.0         # max P(entail) over spans (audit; 0..1)

@dataclass
class XRay:
    question: str
    router_family: str | None
    router_skill: str | None
    fused_pool_ids: list[int]
    top5_ids: list[int]
    ce_scores: dict[int, float]        # item_id -> ce_score (reranked pool)
    tier_primary_miss: bool
    answer: str | None

@dataclass
class Attribution:
    stage: str            # ROUTER|POOL|RANK|COMPOSE|CONFIG|UNRESOLVED
    reason: str

@dataclass
class FactRecord:
    question: str
    stratum: str
    fact_text: str
    vital: bool
    guard_verdict: str
    in_answer: bool
    presence: PresenceResult
    fact_class: str        # IN_ANSWER | OWNED_NOT_SURFACED | NOT_OWNED | DROPPED_ORACLE | NON_SELF_CONTAINED
    stage: str | None
    xray_ref: str
    judge_id: str = ""     # audit: which judge decided presence/in-answer (e.g. 'nli')
    max_score: float = 0.0 # audit: max P(entail) over presence spans
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
