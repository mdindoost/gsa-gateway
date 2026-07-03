# autoeval/generator.py
from __future__ import annotations
import json, hashlib
from pathlib import Path
from autoeval.models import SourceItem, GeneratedQuestion, ExpectedSpec

RAW_DIR = Path(__file__).resolve().parent / "codex_raw"

_ARM_INSTRUCTIONS = """You generate EVALUATION questions about ONE known item to stress-test a
university assistant. You are NOT answering; you produce questions plus a machine-checkable
`expected` spec derived ONLY from the KNOWN FACTS below.

Produce three arms:
- arm "answer": 3 questions whose answer IS in the known facts. expected.type is one of
  contact/count/metric/list, with expected.value (or members) taken verbatim from the facts.
- arm "noisy": for EACH answer question, 1-2 degraded variants (typo/wording/esl/truncation).
  Set variant_type and twin_ref (copy the exact answer-arm question text). SAME expected spec.
- arm "out_of_scope": 2 questions that CANNOT be answered from the facts (a fabricated person,
  an uncovered policy, a subjective 'who is best', OR a field listed in missing_fields). expected.type
  = abstain_or_clarify; if it targets a genuinely missing field, set expected.missing_field.

Ask ONLY questions a real student would ask in plain language ("what is X's email", "who leads Y",
"what kind of office is Z"). NEVER ask about the record's internal structure — no questions about how
many aliases/fields/ids it has, which fields are available, or what the data schema contains.

Return JSON matching the schema. Every question MUST carry a checkable expected spec."""

def build_prompt(item: SourceItem) -> str:
    # NOTE: has_fields is deliberately NOT exposed — handing the generator a field-name list made it
    # ask "which fields are available"-style meta-questions. missing_fields IS exposed because the
    # out-of-scope arm needs to know which real fields to target for abstention.
    facts = json.dumps({"item_type": item.item_type, "name": item.display_name,
                        "known_facts": item.ground_truth,
                        "missing_fields": item.missing_fields}, indent=2)
    return f"{_ARM_INSTRUCTIONS}\n\nKNOWN FACTS:\n{facts}\n"

def _checkable(exp: dict) -> bool:
    t = exp.get("type")
    if t in ("contact", "count", "metric", "entity"):
        return bool(exp.get("value"))
    if t == "list":
        return bool(exp.get("members"))
    if t in ("abstain_or_clarify", "prose"):
        return True
    return False

def _raw_ref(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]

def parse_and_validate(raw: dict, item: SourceItem) -> list[GeneratedQuestion]:
    ref = _raw_ref(raw)
    out: list[GeneratedQuestion] = []
    for q in raw.get("questions", []):
        exp = q.get("expected") or {}
        if not q.get("question") or not _checkable(exp):
            continue  # drop spec-less questions (loud: caller logs count dropped)
        spec = ExpectedSpec(
            type=exp["type"], item_key=item.item_key,          # key ALWAYS from the item, never Codex
            value=exp.get("value"), must_contain_field=exp.get("must_contain_field"),
            members=(exp.get("members") or []), skill_hint=exp.get("skill_hint"),
            missing_field=exp.get("missing_field"))
        out.append(GeneratedQuestion(
            arm=q["arm"], variant_type=q.get("variant_type"), twin_ref=q.get("twin_ref"),
            question_text=q["question"], expected=spec, item_type=item.item_type,
            item_key=item.item_key, codex_raw_ref=ref))
    return out

async def generate(item: SourceItem, run_codex_fn=None) -> list[GeneratedQuestion]:
    from autoeval.codex_client import run_codex
    fn = run_codex_fn or run_codex
    raw = fn(build_prompt(item))            # may raise RateLimitError -> caller pauses
    ref = _raw_ref(raw)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{ref}.json"
    if not raw_path.exists():
        raw_path.write_text(json.dumps({"prompt": build_prompt(item), "response": raw}, indent=2))
    return parse_and_validate(raw, item)
