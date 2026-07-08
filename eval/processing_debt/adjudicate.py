# eval/processing_debt/adjudicate.py
"""Human-adjudication CSV + Cohen's kappa, KEY-BASED (R8 / Fable Guardrail A).

The machine/human agreement (kappa) is joined on a stable ``fact_id`` — NOT row position —
because a human rejecting or adding a nugget de-aligns any positional pairing. Also emits
DROPPED_ORACLE facts with a ``human_guard_ok`` rescue column (R14 B1) and computes nugget-set
decompose-quality (precision/recall) that kappa is blind to. Read-only: never touches the DB.
"""
from __future__ import annotations
import csv
import hashlib

_FS = "␟"  # ␟ unit-separator glyph joining question+fact_text for the id


def fact_id(question: str, fact_text: str) -> str:
    return hashlib.sha1((f"{question}{_FS}{fact_text}").encode("utf-8")).hexdigest()


def cohen_kappa(machine: list[bool], human: list[bool]) -> float:
    n = len(machine)
    assert n == len(human) and n > 0
    po = sum(1 for m, h in zip(machine, human) if m == h) / n
    pm_t = sum(machine) / n
    ph_t = sum(human) / n
    pe = pm_t * ph_t + (1 - pm_t) * (1 - ph_t)
    if pe == 1.0:                      # no variance (all same) → identical labels are perfect
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


CSV_HEADER = [
    "fact_id", "idx", "question", "fact_text", "vital", "guard_verdict",
    "machine_in_answer", "machine_presence", "machine_class", "machine_stage",
    "machine_low_conf", "machine_max_score", "judge_id", "machine_probes",   # audit (Fable fold-in)
    "human_in_answer", "human_presence", "human_stage_ok",
    "human_guard_ok", "human_nugget_ok", "human_missing_nuggets",
]


def emit_csv(records, path: str) -> None:
    """Emit EVERY record (including DROPPED_ORACLE, per B1) keyed by a stable ``fact_id``.

    Human columns are left blank for the adjudicator: ``human_in_answer`` / ``human_presence``
    (kappa), ``human_guard_ok`` (B1 rescue of a wrongly-dropped fact), ``human_nugget_ok`` /
    ``human_missing_nuggets`` (Guardrail A decompose-quality).
    """
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for i, r in enumerate(records):
            pres = r.presence
            w.writerow([
                fact_id(r.question, r.fact_text), i, r.question, r.fact_text,
                int(r.vital), r.guard_verdict,
                int(r.in_answer), int(pres.present), r.fact_class, r.stage or "",
                int(getattr(pres, "low_conf", False)), getattr(pres, "max_score", 0.0),
                r.judge_id, "|".join(pres.probes_hit),
                "", "", "", "", "", "",
            ])


def _truthy(s: str) -> bool:
    return s.strip().lower() in ("1", "true", "yes", "y", "t")


def ingest_labels(path: str) -> dict:
    """Key-based read of the human columns.

    Returns ``{"in_answer": {fact_id: bool}, "presence": {fact_id: bool},
    "guard_ok": {fact_id: bool}, "nugget_ok": {fact_id: bool}, "missing": {question: [str]}}``.
    Only cells the human actually filled are recorded (blank == unlabeled, never coerced).
    """
    out = {"in_answer": {}, "presence": {}, "guard_ok": {}, "nugget_ok": {}, "missing": {}}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            fid = (row.get("fact_id") or "").strip()
            if not fid:
                continue
            for col, key in (("human_in_answer", "in_answer"), ("human_presence", "presence"),
                             ("human_guard_ok", "guard_ok"), ("human_nugget_ok", "nugget_ok")):
                val = (row.get(col) or "").strip()
                if val != "":
                    out[key][fid] = _truthy(val)
            miss = (row.get("human_missing_nuggets") or "").strip()
            if miss:
                q = row.get("question") or ""
                parts = [m.strip() for m in miss.replace("\n", ";").split(";") if m.strip()]
                if parts:
                    out["missing"].setdefault(q, []).extend(parts)
    return out


# κ measures agreement on the presence/in-answer decision only. Facts excluded up front
# (dangling anaphors) or dropped at the oracle-guard never received that decision, so they
# must NOT enter κ (Fable ruling: κ over judgeable facts only).
_NON_JUDGEABLE = {"NON_SELF_CONTAINED", "DROPPED_ORACLE"}

def machine_decisions(records, decision: str) -> dict:
    """Build ``{fact_id: machine_bool}`` for a decision ('in_answer' or 'presence'),
    excluding non-judgeable classes so they can't depress κ."""
    assert decision in ("in_answer", "presence")
    out = {}
    for r in records:
        if r.fact_class in _NON_JUDGEABLE:
            continue
        fid = fact_id(r.question, r.fact_text)
        out[fid] = bool(r.in_answer) if decision == "in_answer" else bool(r.presence.present)
    return out


def paired(machine_by_id: dict, human_by_id: dict) -> tuple[list, list]:
    """Inner-join machine & human decisions on ``fact_id`` → aligned (machine, human) bool lists.

    Only facts the human labeled AND the machine emitted are kept, so a rejected or added nugget
    never de-aligns the pairing (the whole point of R8). Iteration order follows ``machine_by_id``.
    """
    keys = [k for k in machine_by_id if k in human_by_id]
    return ([bool(machine_by_id[k]) for k in keys], [bool(human_by_id[k]) for k in keys])


def nugget_quality(records, human: dict) -> dict:
    """Guardrail A decompose-quality.

    A machine nugget counts as *accepted* unless the human explicitly marked ``human_nugget_ok``
    False (blank == accepted — the human only flags bad ones). ``added`` = count of human-supplied
    ``human_missing_nuggets`` across all questions.
        precision = accepted / total_machine
        recall    = accepted / (accepted + added)
    Empty inputs are vacuously perfect (1.0) to avoid divide-by-zero.
    """
    total_machine = len(records)
    nugget_ok = human.get("nugget_ok", {})
    rejected = sum(1 for r in records if nugget_ok.get(fact_id(r.question, r.fact_text)) is False)
    accepted = total_machine - rejected
    added = sum(len(v) for v in human.get("missing", {}).values())
    precision = accepted / total_machine if total_machine else 1.0
    recall = accepted / (accepted + added) if (accepted + added) else 1.0
    return {"total_machine": total_machine, "accepted": accepted, "added": added,
            "rejected": rejected, "precision": precision, "recall": recall}
