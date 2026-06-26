"""Tests for the answer-gate (spec §13.6 — the hybrid two-gate confidence design).

Gate 1 = deterministic, pre-retrieval INTENT cues (personal/account, do-a-task, other-institution,
live+personal-referent). Fires => deflect immediately, skip fallback (spec fold #5).
Gate 2 = LLM answerability check (evidence-first graded). Tested here at the prompt/parse/decision
level only (the LLM call itself is exercised by the shadow runner).

IMPORTANT: fixtures here use ILLUSTRATIVE sentences, NOT the frozen eval/*.txt lines, so the frozen
instrument stays an independent measurement oracle (spec fold #1: never tune cues against it).
"""
from v2.core.retrieval.answer_gate import (
    gate1_intent,
    gate2_prompt,
    parse_gate2,
    gate_decision,
)


# ---------------------------------------------------------------- Gate 1: personal/account
def test_gate1_fires_on_personal_status_query():
    v = gate1_intent("what is my account balance")
    assert v.deflect is True and v.cue == "personal"


def test_gate1_fires_on_has_my_record_approved():
    assert gate1_intent("has my visa document been approved").deflect is True


def test_gate1_fires_on_how_much_do_i_owe():
    assert gate1_intent("how much do I owe on my account").deflect is True


def test_gate1_does_not_fire_on_general_requirement_with_my():
    # possessive "my degree" but asks a GENERAL requirement, not a private record value
    v = gate1_intent("how many credits do I need to finish my degree")
    assert v.deflect is False


def test_gate1_does_not_fire_on_my_responsibilities():
    assert gate1_intent("what are my responsibilities as an officer").deflect is False


def test_gate1_does_not_fire_on_process_question_with_record_noun():
    # "how do I request my transcript" is a PROCESS question, not a status query
    assert gate1_intent("how do I request my transcript").deflect is False


def test_gate1_does_not_fire_on_policy_question_mentioning_my_account():
    # "why is there an AR hold on my account" asks a POLICY question — answerable, not a private value
    assert gate1_intent("why is there an AR hold on my student account").deflect is False


def test_gate1_does_not_fire_on_conditional_policy_with_my_account():
    q = "if I make a partial payment on my account will I still be charged a late fee"
    assert gate1_intent(q).deflect is False


def test_gate1_still_fires_on_listing_whats_on_my_account():
    # the state-listing frame ("what holds are on my account") IS a private-record query
    assert gate1_intent("what holds are on my student account").deflect is True


# ---------------------------------------------------------------- Gate 1: do-a-task
def test_gate1_fires_on_write_my_deliverable():
    v = gate1_intent("write my personal essay for me")
    assert v.deflect is True and v.cue == "task"


def test_gate1_fires_on_do_my_homework():
    assert gate1_intent("do my homework assignment").deflect is True


def test_gate1_does_not_fire_on_how_do_i_write():
    # guidance about writing is answerable, not a task to perform
    assert gate1_intent("how do I write a strong cover letter").deflect is False


# ---------------------------------------------------------------- Gate 1: other-institution
def test_gate1_fires_on_other_institution():
    v = gate1_intent("what is the tuition at Rutgers")
    assert v.deflect is True and v.cue == "other_institution"


def test_gate1_exempts_transfer_into_njit():
    # "transfer credits from <school> to NJIT" is in-scope
    assert gate1_intent("how do I transfer credits from Rutgers to NJIT").deflect is False


# ---------------------------------------------------------------- Gate 1: live + personal referent
def test_gate1_does_not_fire_on_events_today():
    # bare time-cue must NOT fire (events/food carve-out)
    assert gate1_intent("what events are on campus today").deflect is False


# ---------------------------------------------------------------- Gate 2: prompt construction
def test_gate2_prompt_includes_question_and_context():
    sys_p, user_p = gate2_prompt("when is the deadline", ["The deadline is May 1."])
    assert "when is the deadline" in user_p
    assert "The deadline is May 1." in user_p
    # evidence-first: must ask for a supporting quote and a graded label
    assert "quote" in sys_p.lower()
    assert "NOT_IN_CONTEXT" in sys_p


# ---------------------------------------------------------------- Gate 2: parse
def test_parse_gate2_reads_label_and_quote():
    raw = '{"supporting_quote": "The deadline is May 1.", "label": "FULLY_SUPPORTED", "missing_piece": ""}'
    v = parse_gate2(raw)
    assert v.label == "FULLY_SUPPORTED" and v.quote == "The deadline is May 1."


def test_parse_gate2_tolerates_surrounding_text():
    raw = 'Here is my verdict:\n{"label": "NOT_IN_CONTEXT", "supporting_quote": "", "missing_piece": "the fee"}\nThanks'
    assert parse_gate2(raw).label == "NOT_IN_CONTEXT"


def test_parse_gate2_defaults_to_answerable_on_garbage():
    # answer-biased default: unparseable => treat as supported (never withhold on a parse failure)
    v = parse_gate2("the model rambled with no json")
    assert v.label == "FULLY_SUPPORTED"


# ---------------------------------------------------------------- decision: gate-the-gate + ordering
def test_decision_gate1_hit_is_terminal_deflect():
    d = gate_decision(gate1_cue="personal", ce_score=0.99, gate2_label=None, band=0.70)
    assert d.outcome == "deflect" and d.skip_fallback is True


def test_decision_skips_gate2_above_band():
    # confident retrieval (high ce) => do NOT run gate 2 (gate-the-gate); answer
    d = gate_decision(gate1_cue=None, ce_score=0.95, gate2_label=None, band=0.70)
    assert d.outcome == "answer" and d.run_gate2 is False


def test_decision_runs_gate2_in_low_ce_band():
    d = gate_decision(gate1_cue=None, ce_score=0.30, gate2_label=None, band=0.70)
    assert d.run_gate2 is True


def test_decision_not_in_context_routes_to_fallback_not_deflect():
    # Gate-2 NOT_IN_CONTEXT is NEVER terminal — routes to fallback (spec fold #5)
    d = gate_decision(gate1_cue=None, ce_score=0.30, gate2_label="NOT_IN_CONTEXT", band=0.70)
    assert d.outcome == "fallback" and d.skip_fallback is False


def test_decision_supported_answers():
    d = gate_decision(gate1_cue=None, ce_score=0.30, gate2_label="FULLY_SUPPORTED", band=0.70)
    assert d.outcome == "answer"
