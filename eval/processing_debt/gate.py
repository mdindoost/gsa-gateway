# eval/processing_debt/gate.py
"""Control gate — a REAL halt before any full-set spend (R10). Refuses the run unless the controls pass.

  SC2: positive-control owned-misses <= 1  (the decompose/materiality/presence chain works end-to-end)
  SC3: every oracle-blind fact is guard-flagged  (the guard doesn't pass GSA-internal facts as oracle-known)
  M6:  the Brave Answers endpoint is reachable  (require_oracle_reachable)
  EMBED: query-embedder width matches the live corpus  (verify_embedding_alignment — the .env/dim guard)

B2: SC2/SC3 are meant to run on HUMAN-CONFIRMED control facts (the caller adjudicates the ~8 control Qs
first) so the IN_ANSWER unsure-lean can't manufacture a false positive-control miss. Read-only.
"""
from __future__ import annotations
import sqlite3
import sys
from dataclasses import dataclass, field


@dataclass
class GateResult:
    sc2_pass: bool
    sc3_pass: bool
    positive_owned_misses: int
    oracle_blind_total: int
    oracle_blind_flagged: int
    reasons: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.sc2_pass and self.sc3_pass


def evaluate_control_gate(records) -> GateResult:
    pos_miss = [r for r in records if r.vital and r.stratum == "positive_control"
                and r.fact_class == "OWNED_NOT_SURFACED"]
    blind = [r for r in records if r.vital and r.stratum == "oracle_blind"]
    flagged = [r for r in blind if r.fact_class == "DROPPED_ORACLE"]
    sc2 = len(pos_miss) <= 1
    sc3 = len(flagged) == len(blind)          # vacuously true if no blind facts
    reasons = []
    if not sc2:
        reasons.append(f"SC2 FAIL: {len(pos_miss)} positive-control owned-misses (>1) — the "
                       "decompose/materiality/presence chain is broken; DO NOT spend on the full set")
    if not sc3:
        reasons.append(f"SC3 FAIL: {len(blind) - len(flagged)}/{len(blind)} oracle-blind facts NOT "
                       "guard-flagged — the guard is treating GSA-internal facts as oracle-known")
    return GateResult(sc2, sc3, len(pos_miss), len(blind), len(flagged), reasons)


def _hard_exit(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def enforce_control_gate(records, *, exit_fn=None) -> GateResult:
    """R10: a REAL halt. On SC2/SC3 failure, call exit_fn(msg) (default: print to stderr + sys.exit(2))."""
    res = evaluate_control_gate(records)
    if not res.passed:
        (exit_fn or _hard_exit)("CONTROL GATE FAILED — refusing the full run:\n  " + "\n  ".join(res.reasons))
    return res


def _default_oracle_probe() -> bool:
    """One cached, minimal live oracle call. True iff it returns an answer. (Costs ~1 Brave query.)"""
    try:
        from eval.processing_debt.oracle_brave import ask_oracle
        oa = ask_oracle("what is the capital of New Jersey")
        return bool(oa and getattr(oa, "answer", ""))
    except Exception:
        return False


def require_oracle_reachable(*, probe=None, exit_fn=None) -> None:
    """M6: hard precondition — the Brave Answers endpoint must be reachable before any spend."""
    if not (probe or _default_oracle_probe)():
        (exit_fn or _hard_exit)("M6 FAIL: Brave Answers endpoint not reachable — refusing the run")


def verify_embedding_alignment(conn, *, embed_knn=None, exit_fn=None) -> None:
    """Fail-loud guard (senior-review MUST-FIX #2 + the B5 finding): the query embedder MUST match the live
    corpus width. If the pilot process didn't load EMBEDDING_MODEL (.env), the 768-vs-1024 mismatch makes
    the production KNN silently return [] → false NOT_OWNED/POOL verdicts. Run a probe query on a
    corpus-common term through the real KNN; halt if it returns 0 hits (or raises)."""
    try:
        if embed_knn is None:
            from eval.processing_debt.presence_check import _real_embed_and_knn
            eq, knn = _real_embed_and_knn()

            def embed_knn():
                v = eq("computer science")
                return knn(conn, v, k=3) if v is not None else []
        hits = embed_knn()
    except Exception as e:                    # noqa: BLE001 — any probe failure is a hard stop
        (exit_fn or _hard_exit)(f"EMBED ALIGN FAIL: probe raised {e!r} — refusing the run")
        return
    if not hits:
        (exit_fn or _hard_exit)(
            "EMBED ALIGN FAIL: query-embedding KNN returned 0 hits on a corpus-common term. The pilot "
            "process likely did not load EMBEDDING_MODEL (.env) → a 768-vs-1024 width mismatch silently "
            "empties retrieval. Refusing the run.")
