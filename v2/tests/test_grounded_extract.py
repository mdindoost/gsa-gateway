from v2.core.ingestion.grounded_extract import answer_from_page, ground_spans, build_extract_prompt

PAGE = ("Graduate Admission. The application fee is $75 and is non-refundable. "
        "International applicants must submit TOEFL scores with a minimum of 79.")


def test_keeps_verbatim_span_present_on_page():
    llm = lambda s, u: '{"spans": ["The application fee is $75 and is non-refundable."]}'
    ans = answer_from_page("how much is the fee", PAGE, "https://njit.edu/x", llm)
    assert ans is not None
    assert ans.spans == ["The application fee is $75 and is non-refundable."]
    assert ans.source_url == "https://njit.edu/x"


def test_drops_hallucinated_span_not_on_page():
    # the model invents a fee that is NOT on the page -> must be dropped -> None
    llm = lambda s, u: '{"spans": ["The application fee is $200."]}'
    assert answer_from_page("fee", PAGE, "https://njit.edu/x", llm) is None


def test_none_when_no_spans():
    llm = lambda s, u: '{"spans": []}'
    assert answer_from_page("parking", PAGE, "https://njit.edu/x", llm) is None


def test_none_on_bad_json():
    llm = lambda s, u: 'not json'
    assert answer_from_page("fee", PAGE, "https://njit.edu/x", llm) is None


def test_prompt_contains_question_and_page():
    sys, user = build_extract_prompt("how much is the fee", PAGE)
    assert "how much is the fee" in user and "$75" in user and "VERBATIM" in sys.upper()
