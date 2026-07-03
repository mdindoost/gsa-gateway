# autoeval/models.py
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class ExpectedSpec:
    type: str                      # contact | count | metric | list | abstain_or_clarify | prose | entity
    item_key: str                  # family-typed: person nodes.key OR str(org_id) OR area string
    value: Optional[str] = None    # the expected value (email, number, etc.)
    must_contain_field: Optional[str] = None   # e.g. "email"
    members: list[str] = field(default_factory=list)   # for type=list
    skill_hint: Optional[str] = None
    missing_field: Optional[str] = None          # set when arm C targets a genuinely absent field

@dataclass
class SourceItem:
    item_type: str                 # person | org | area | chunk
    item_key: str                  # family-typed key (see ExpectedSpec.item_key)
    display_name: str
    ground_truth: dict[str, Any]
    has_fields: list[str]
    missing_fields: list[str]

@dataclass
class GeneratedQuestion:
    arm: str                       # answer | noisy | out_of_scope
    variant_type: Optional[str]    # typo | wording | esl | truncation (arm B only)
    twin_ref: Optional[str]        # arm B: a stable ref to its arm-A twin question_text
    question_text: str
    expected: ExpectedSpec
    item_type: str
    item_key: str
    codex_raw_ref: str             # path/hash of the stored raw codex response

@dataclass
class KavoshObservation:
    answer_text: str
    used_ai: bool
    is_live: bool
    is_deep: bool
    source_note: Optional[str]
    family: Optional[str]          # from decide(): KG|RAG|LIVE|CLARIFY|COMMAND|OTHER
    skill: Optional[str]
    resolved_key: Optional[str]    # family-aware: entity_id for person skills, str(org_id) for org
    slot_extracted: bool           # decide() went through LLM slot extraction (fidelity caveat)
    is_abstain: bool               # text matched a canned abstain string
    is_clarify: bool               # text matched the clarify string
    latency_ms: int

@dataclass
class CheckOutcome:
    result: str                    # pass | fail
    failure_class: Optional[str]   # None | fabrication | resolution_failure | routing_failure
    data_gap: bool
    evidence: dict[str, Any]
    graded_soft: bool = False
    llm_judge_verdict: Optional[str] = None
    llm_judge_confidence: Optional[float] = None
