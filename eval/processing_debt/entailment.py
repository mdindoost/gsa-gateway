"""Entailment judge for the processing-debt instrument.

Judge fix (2026-07-07, Fable gate): the presence check's judge was granite4:tiny-h, which
hedged 'unsure' on nearly every pair (incl. unrelated ones) and, under the old unsure->present
lean, inflated debt ~2x. Now the DEFAULT judge is the calibrated NLI cross-encoder (nli_judge.py);
generative backends (llama/gemma/granite) remain env-selectable but use the STRICTER prompt.

Interfaces:
  entail_verdict(fact, text, gen=None) -> 'yes'|'no'|'unsure'  (single pair; used by IN_ANSWER)
  entails(fact, text, gen=None) -> bool                        (== 'yes')
  batch_verdicts(fact, spans, judge=None) -> [(verdict, score)]  (BATCHED; used by presence_check)
  score_to_verdict(p) / active_judge_id() / generative_verdict(...)

FAIL LOUD: when the NLI judge is selected and returns None (load/inference failure), batch_verdicts
RAISES — it must NEVER silently fall back to the weak granite judge and reinstate the bug.
"""
from __future__ import annotations
import os

_SCHEMA = {"type": "object",
           "properties": {"verdict": {"type": "string", "enum": ["yes", "no", "unsure"]}},
           "required": ["verdict"]}

# Legacy prompt (kept for the back-compat gen-injection path / existing tests).
_SYSTEM = ("You are a strict entailment judge. Decide whether the TEXT supports the CLAIM. "
           "Answer 'yes' if the text states or directly implies the claim; 'no' if it contradicts or is "
           "clearly silent on it; 'unsure' if the text is related but only partially or ambiguously "
           "supports it. Answer only via the schema.")

# Improved prompt for ALL generative backends — kills the lazy 'unsure' default that caused the bug.
IMPROVED_SYSTEM = (
    "You are a strict entailment judge. Decide whether the TEXT supports the CLAIM. "
    "Answer 'yes' only if the TEXT states or directly implies the CLAIM. "
    "Answer 'no' when the TEXT is about a DIFFERENT subject, person, or topic than the CLAIM, "
    "or is silent on it. If the TEXT is about a different person or entity than the CLAIM, that is "
    "always 'no', never 'unsure'. Reserve 'unsure' ONLY for the SAME subject where support is partial "
    "or ambiguous. Answer only via the schema.")

# Calibrated P(entail) thresholds (env-overridable; pre-registered defaults per Fable gate).
HI = float(os.environ.get("PD_ENTAIL_HI", "0.5"))
LO = float(os.environ.get("PD_ENTAIL_LO", "0.35"))
# The oracle-guard is a "does the oracle's OWN citation support this" check — deliberately LENIENT
# (a false-DROP deletes a measurable fact + breaks positive controls; a false-KEEP lands harmlessly
# in NOT_OWNED, excluded from the headline). Keep unless CLEARLY unrelated. Independently tunable.
GUARD_LO = float(os.environ.get("PD_GUARD_LO", str(LO)))


def active_judge_id() -> str:
    """Identity of the configured judge (audited per-fact). Default = calibrated NLI."""
    return os.environ.get("PD_JUDGE", "nli")


def score_to_verdict(p: float, hi: float = HI, lo: float = LO) -> str:
    if p >= hi:
        return "yes"
    if p >= lo:
        return "unsure"
    return "no"


# ---- generative backends (llama/gemma/granite) ----
def _make_gen(model: str):
    def gen(system, prompt, schema):
        from bot.services.ollama_client import generate_json_sync
        return generate_json_sync(system, prompt, schema, model=model, timeout=60.0, num_predict=16)
    return gen


def generative_verdict(fact: str, text: str, *, model: str, gen=None) -> str:
    """One (fact, text) pair via a generative model using the STRICTER prompt. Fail-safe = 'no'."""
    gen = gen or _make_gen(model)
    prompt = f"CLAIM:\n{fact}\n\nTEXT:\n{text}\n\nIs the CLAIM supported by the TEXT?"
    out = gen(IMPROVED_SYSTEM, prompt, _SCHEMA)
    v = (out or {}).get("verdict")
    return v if v in ("yes", "no", "unsure") else "no"


def _get_judge():
    from eval.processing_debt.nli_judge import get_judge
    return get_judge()


def batch_verdicts(fact: str, spans: list[str], *, judge=None) -> list[tuple[str, float]]:
    """Score ALL spans for one fact in a single call; return [(verdict, P_entail), ...] in order.

    NLI path (default): one batched inference. FAIL LOUD if the judge returns None — never
    silently degrade to granite. Generative path: per-span verdict via the improved prompt
    (pseudo-scores yes=1.0/unsure=0.5/no=0.0 for uniform downstream handling)."""
    if not spans:
        return []
    jid = active_judge_id()
    if jid == "nli":
        judge = judge or _get_judge()
        scores = judge.score(fact, spans)
        if scores is None:
            raise RuntimeError("NLI judge unavailable (JUDGE_ERROR) — refusing to silently "
                               "fall back to a weaker judge; fix the model or set PD_JUDGE explicitly")
        return [(score_to_verdict(s), s) for s in scores]
    # generative backend
    _pseudo = {"yes": 1.0, "unsure": 0.5, "no": 0.0}
    out = []
    for s in spans:
        v = generative_verdict(fact, s, model=jid)
        out.append((v, _pseudo[v]))
    return out


def entail_verdict(fact: str, text: str, *, gen=None) -> str:
    """Single-pair verdict 'yes'|'no'|'unsure'. Used by the IN_ANSWER check.
    - gen injected (tests / explicit generative) -> legacy schema path.
    - else NLI when selected; else the configured generative backend. Fail-safe on model failure = 'no'."""
    if gen is not None:
        prompt = f"CLAIM:\n{fact}\n\nTEXT:\n{text}\n\nIs the CLAIM supported by the TEXT?"
        out = gen(_SYSTEM, prompt, _SCHEMA)
        v = (out or {}).get("verdict")
        return v if v in ("yes", "no", "unsure") else "no"
    jid = active_judge_id()
    if jid == "nli":
        scores = _get_judge().score(fact, [text])
        if scores is None:
            raise RuntimeError("NLI judge unavailable (JUDGE_ERROR) — refusing silent fallback")
        return score_to_verdict(scores[0])
    return generative_verdict(fact, text, model=jid)


def entails(fact: str, text: str, *, gen=None) -> bool:
    """Hard boolean = (verdict == 'yes'). Single-pair, UNwindowed — kept for back-compat; prefer the
    windowed helpers below for IN_ANSWER / guard where `text` can exceed the 512-token NLI cap."""
    return entail_verdict(fact, text, gen=gen) == "yes"


def _windowed_verdicts(fact: str, text: str, *, judge=None) -> list[tuple[str, float]]:
    """Split `text` into ≤512-token windows (B3) and score every window in one batch. Long docs
    (bot rosters, fetched pages) would otherwise be head-truncated by the NLI model."""
    from eval.processing_debt.presence_check import _nli_windows   # lazy: avoids import cycle
    wins = _nli_windows(text or "", fact)
    if not wins:
        return []
    return batch_verdicts(fact, wins, judge=judge)


def text_entails_fact(fact: str, text: str, *, judge=None) -> bool:
    """IN_ANSWER: does `text` (windowed) CONFIDENTLY entail `fact`? Any window verdict == 'yes' (P≥HI).
    Same B1-validated 'does this text contain this fact' semantics as the presence check."""
    return any(v == "yes" for v, _ in _windowed_verdicts(fact, text, judge=judge))


def supported_by(fact: str, text: str, *, judge=None, lo: float | None = None) -> bool:
    """GUARD: keep the fact unless `text` (windowed) is CLEARLY unrelated. Any window P(entail) ≥ GUARD_LO.
    Lenient by design (see GUARD_LO) — strict entailment here wrongly drops loosely-cited real facts."""
    lo = GUARD_LO if lo is None else lo
    return any(sc >= lo for _, sc in _windowed_verdicts(fact, text, judge=judge))
