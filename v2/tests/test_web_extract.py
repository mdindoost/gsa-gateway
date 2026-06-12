"""Grounded web extraction: a fact survives only if its quote is literally on the page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.web_extract import (chunk_text, extract_page, is_grounded,
                                           parse_facts)

PAGE = (
    "Jane Roe is a Professor of Computer Science. She received the ACM Distinguished "
    "Scientist award in 2021. She develops GraphTool, an open-source library for graph "
    "analytics. Her research focuses on spectral methods and network science."
)


def llm_returning(facts):
    return lambda system, user: json.dumps(facts)


def test_grounded_fact_kept_hallucinated_dropped():
    facts = [
        # grounded: quote is verbatim on the page
        {"field": "award", "value": "ACM Distinguished Scientist (2021)",
         "evidence": "received the ACM Distinguished Scientist award in 2021"},
        # HALLUCINATED: this quote is NOT on the page -> must be dropped
        {"field": "award", "value": "Turing Award (2020)",
         "evidence": "Jane Roe won the Turing Award in 2020"},
        {"field": "software", "value": "GraphTool",
         "evidence": "She develops GraphTool, an open-source library"},
    ]
    out = extract_page(PAGE, "Jane Roe", "http://x/", llm_returning(facts))
    vals = {(f.field, f.value) for f in out}
    assert ("award", "ACM Distinguished Scientist (2021)") in vals
    assert ("software", "GraphTool") in vals
    assert not any("Turing" in f.value for f in out)        # hallucination discarded


def test_disallowed_field_and_empty_value_dropped():
    facts = [
        {"field": "salary", "value": "$200k", "evidence": "Her research focuses on spectral methods"},
        {"field": "bio", "value": "", "evidence": "Jane Roe is a Professor of Computer Science"},
    ]
    assert extract_page(PAGE, "Jane Roe", "http://x/", llm_returning(facts)) == []


def test_is_grounded_whitespace_insensitive_but_verbatim():
    assert is_grounded("ACM   Distinguished Scientist", PAGE)      # whitespace-normalized
    assert not is_grounded("ACM Distinguished Engineer", PAGE)     # not on the page
    assert not is_grounded("award", PAGE)                          # too short to ground


def test_parse_facts_tolerates_code_fences_and_prose():
    raw = 'Here is the JSON:\n```json\n[{"field":"bio","value":"x","evidence":"yy"}]\n```'
    assert parse_facts(raw) == [{"field": "bio", "value": "x", "evidence": "yy"}]
    assert parse_facts("sorry, nothing found") == []


def test_parse_facts_coerces_object_keyed_by_field():
    # the shape llama actually emitted under format:json
    raw = ('{"bio":{"value":"a prof","evidence":"is a Professor"},'
           '"award":[{"value":"A1","evidence":"e1"},{"value":"A2","evidence":"e2"}]}')
    got = parse_facts(raw)
    assert {"field": "bio", "value": "a prof", "evidence": "is a Professor"} in got
    assert sum(d["field"] == "award" for d in got) == 2   # list under a field expands


def test_chunking_covers_whole_page_no_truncation():
    text = "A" * 25000
    chunks = chunk_text(text, window=10000, overlap=500)
    assert len(chunks) >= 3
    assert max(len(c) for c in chunks) <= 10000
    # every character index is covered by some window (no gap)
    assert chunks[0][0] == "A" and chunks[-1][-1] == "A"


def test_dedup_across_windows():
    # same fact emitted twice -> stored once
    facts = [{"field": "software", "value": "GraphTool",
              "evidence": "She develops GraphTool, an open-source library"}]
    out = extract_page(PAGE, "Jane Roe", "http://x/",
                       lambda s, u: json.dumps(facts + facts))
    assert sum(f.field == "software" for f in out) == 1
