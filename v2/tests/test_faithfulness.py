"""Tests for the WS4 post-generation faithfulness/answerability gate (v2/core/retrieval/faithfulness.py).

The gate decides answer|abstain on a COMPOSED RAG answer, using deterministic answer-type grounding
(count/rate/money/date), a subjective-superlative guard, robust markdown-normalized quote grounding
(replaces the brittle contiguous-substring quote_grounded that caused the Chrome-River false-abstain),
and — only for the non-typed factual residual — a Gate-2 answerability verdict supplied by the caller.

Fixtures use ILLUSTRATIVE sentences, NOT the frozen eval/*.txt lines (spec fold #1).
"""
from v2.core.retrieval.faithfulness import (
    is_subjective,
    is_explicit_nonanswer,
    expected_answer_type,
    answer_has_grounded_type,
    robust_grounded,
    assess_pre_gate2,
    decide_after_gate2,
)


# ------------------------------------------------------------------ subjective superlative guard
def test_subjective_best_professor():
    assert is_subjective("who is the best professor at NJIT") is True


def test_subjective_easiest_program():
    assert is_subjective("which graduate program is the easiest") is True


def test_not_subjective_factual_count():
    assert is_subjective("how many credits do I need to graduate") is False


def test_not_subjective_best_way_to():
    # RAG review #6: "best way/time to X" is an answerable how-to, NOT a subjective superlative
    assert is_subjective("what is the best way to register for classes") is False
    assert is_subjective("what is the best time to apply for graduation") is False


# ------------------------------------------------------------------ expected answer type
def test_type_count_how_many():
    assert expected_answer_type("how many days after travel must I submit the report") == "count"


def test_type_rate_pass_rate():
    assert expected_answer_type("what is the pass rate for the qualifying exam") == "rate"


def test_type_money_tuition():
    assert expected_answer_type("how much is graduate tuition at NJIT") == "money"


def test_type_date_deadline():
    assert expected_answer_type("what is the deadline to apply for graduation") == "date"


def test_type_none_for_process_question():
    assert expected_answer_type("what are the duties of the vice president") is None


def test_type_how_much_time_is_not_money():
    # RAG review #3: "how much time/longer/notice" must not classify as money (would hard-abstain)
    assert expected_answer_type("how much time do I have to submit the report") != "money"
    assert expected_answer_type("how much longer until graduation") != "money"


# ------------------------------------------------------------------ answer-type grounding
def test_count_grounded_value_present():
    # answer states "30 days", passage contains "30" -> grounded
    ans = "You have **30 days** after travel to submit the report."
    passages = ["Reports must be submitted within 30 days of travel."]
    assert answer_has_grounded_type(ans, passages, "count") is True


def test_count_ignores_year_not_grounded_answer():
    # a pasted answer whose only number is a YEAR must NOT count as a grounded count
    ans = "The Spring 2026 Dean's List includes Abada, Younes and others."
    passages = ["Spring 2026 Dean's List: Abada, Younes; Smith, John."]
    assert answer_has_grounded_type(ans, passages, "count") is False


def test_money_grounded_digit_run():
    ans = "The maximum award is $900 per fiscal year."
    passages = ["Each student is limited to a maximum of $900 per fiscal year."]
    assert answer_has_grounded_type(ans, passages, "money") is True


def test_rate_ungrounded_when_no_percentage():
    ans = "At least two Passes are required from the committee."
    passages = ["Two Passes are required from the qualifying exam committee."]
    assert answer_has_grounded_type(ans, passages, "rate") is False


def test_count_digit_word_equivalence():
    # RAG review #5: answer spells "four", passage has "4" (or vice versa) — still grounded
    assert answer_has_grounded_type("There are four officers.", ["The board has 4 officers."], "count") is True
    assert answer_has_grounded_type("There are 4 officers.", ["The board has four officers."], "count") is True


def test_count_large_number_extracted():
    # RAG review #5: counts >=1000 must be extracted (not dropped)
    assert answer_has_grounded_type("about 11500 students", ["enrollment is 11500 students"], "count") is True


def test_date_relative_duration_grounded():
    # RAG review #4: a 'deadline' answer expressed as a relative duration must count as a grounded date
    ans = "The report is due within 30 days of travel."
    passages = ["submissions must be completed within 30 days of travel."]
    assert answer_has_grounded_type(ans, passages, "date") is True


# ------------------------------------------------------------------ senior-eng review folds
def test_count_large_5digit_grounded():
    # senior #1: 4-6 digit counts (enrollment/headcount) must be extracted, not dropped -> abstained
    assert answer_has_grounded_type(
        "There are about 5000 students enrolled.", ["The program enrolls 5000 students."], "count") is True


def test_money_year_not_grounded():
    # senior #3: a bare YEAR in a cost answer must NOT be certified as a grounded dollar amount
    assert answer_has_grounded_type("contact us in 2024", ["the year 2024 event"], "money") is False


def test_money_no_false_ground_via_digit_substring():
    # senior #4: "$500" must NOT be grounded by "500" appearing inside an unrelated "15003"
    assert answer_has_grounded_type("it costs $500", ["ext 15003 phone"], "money") is False


def test_rate_grounded_despite_percent_spacing():
    # senior #6: "85 %" (answer) vs "85%" (passage) must still ground
    assert answer_has_grounded_type("the pass rate is 85 %", ["pass rate 85%"], "rate") is True


def test_gate2_parse_fail_abstains():
    # WS4 eval-driven (supersedes senior #5): a parse-failed SUPPORTED verdict is out-of-domain garbage
    # at temp 0.0 (it leaked "capital of France") — abstain, not answer.
    outcome, _ = decide_after_gate2("FULLY_SUPPORTED", "", ["some context"], parsed=False)
    assert outcome == "abstain"


def test_gate2_empty_quote_abstains():
    # a SUPPORTED verdict with no supporting quote is a weak-support signal — abstain
    outcome, _ = decide_after_gate2("FULLY_SUPPORTED", "   ", ["some context"])
    assert outcome == "abstain"


# ------------------------------------------------------------------ robust quote grounding (Chrome River fix)
def test_robust_grounded_survives_markdown():
    # the CANONICAL Chrome-River bug: quote is clean text, passage has markdown emphasis
    quote = "submit a Chrome River Expense Report also within 30 days of travel"
    passages = ["students must submit a **Chrome River Expense Report** also within **30 days** of travel."]
    assert robust_grounded(quote, passages) is True


def test_robust_grounded_rejects_absent_quote():
    quote = "the capital of France is Paris"
    passages = ["France is one of the partner countries in the exchange program."]
    assert robust_grounded(quote, passages) is False


# ------------------------------------------------------------------ explicit non-answer (self-abstain)
def test_nonanswer_explicit_decline():
    assert is_explicit_nonanswer("I wasn't able to find that in the knowledge base.") is True


def test_nonanswer_dont_have_exact():
    assert is_explicit_nonanswer("I don't have the exact fee for that program.") is True


def test_nonanswer_false_on_helpful_njit_pointer():
    # a real answer that points to a source is NOT a decline (the looks_like_deflection over-trigger)
    assert is_explicit_nonanswer("To apply, visit admissions.njit.edu and create an account.") is False


def test_nonanswer_false_on_current_hours_pointer():
    assert is_explicit_nonanswer("For current hours, see registrar.njit.edu.") is False


# ------------------------------------------------------------------ pre-gate2 assessment
def test_pre_gate2_subjective_abstains():
    outcome, _ = assess_pre_gate2("who is the best professor", "Dr. Smith is the best.", ["Dr. Smith teaches CS."])
    assert outcome == "abstain"


def test_pre_gate2_typed_grounded_answers():
    outcome, _ = assess_pre_gate2(
        "how much is the maximum award", "The maximum is $900.", ["limited to a maximum of $900 per year"])
    assert outcome == "answer"


def test_pre_gate2_typed_ungrounded_abstains():
    outcome, _ = assess_pre_gate2(
        "what is the pass rate", "At least two Passes are required.", ["Two Passes are required from the QEC."])
    assert outcome == "abstain"


def test_pre_gate2_nontyped_defers_to_gate2():
    outcome, _ = assess_pre_gate2(
        "what are the duties of the president", "The president leads meetings.", ["The president chairs meetings."])
    assert outcome == "gate2"


# ------------------------------------------------------------------ post-gate2 decision
def test_gate2_not_in_context_abstains():
    outcome, _ = decide_after_gate2("NOT_IN_CONTEXT", "", ["some passage"])
    assert outcome == "abstain"


def test_gate2_supported_and_grounded_answers():
    outcome, _ = decide_after_gate2(
        "FULLY_SUPPORTED", "the president chairs meetings", ["the president chairs meetings of the assembly"])
    assert outcome == "answer"


def test_gate2_supported_but_ungrounded_quote_abstains():
    # Granite claims support with a quote that is NOT in the passages -> abstain
    outcome, _ = decide_after_gate2(
        "FULLY_SUPPORTED", "the capital of France is Paris", ["France is a partner country"])
    assert outcome == "abstain"


# ------------------------------------------------------------------ Gate-2 precision fix (positive-span reframe)
# Layer-1 decision contract lock: PARTIALLY_SUPPORTED already routes to answer (given a grounded quote),
# so compound questions (primary answered, secondary detail missing) surface here instead of dying as
# NOT_IN_CONTEXT. These are characterization tests -- no logic change, just locking existing behavior.
_CTX = ["To drop a class, submit the withdrawal form to the Registrar before the deadline."]


def test_partial_support_with_grounded_quote_answers():
    # compound: primary answered (how to drop), secondary (exact deadline) missing -> ANSWER
    out, reason = decide_after_gate2("PARTIALLY_SUPPORTED", "submit the withdrawal form to the Registrar", _CTX)
    assert out == "answer"


def test_full_support_with_grounded_quote_answers():
    out, _ = decide_after_gate2("FULLY_SUPPORTED", "submit the withdrawal form to the Registrar", _CTX)
    assert out == "answer"


def test_not_in_context_abstains():
    out, reason = decide_after_gate2("NOT_IN_CONTEXT", "", _CTX)
    assert out == "abstain" and reason == "gate2:not-in-context"


def test_supported_but_ungrounded_quote_abstains():
    out, reason = decide_after_gate2("FULLY_SUPPORTED", "tuition is due in the patent office", _CTX)
    assert out == "abstain" and reason == "gate2:unsupported"


def test_parse_fail_abstains_even_if_supported():
    out, reason = decide_after_gate2("FULLY_SUPPORTED", "submit the withdrawal form", _CTX, parsed=False)
    assert out == "abstain" and reason == "gate2:unsupported"
