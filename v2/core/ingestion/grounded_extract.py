"""Extractive, span-grounded answering from a fetched page.

The LLM SELECTS verbatim spans from the page that answer the question; we keep a span only
if it appears literally on the page (whitespace-normalized substring). No generative rewrite,
so the combination/paraphrase hallucination class cannot occur. Returns None if nothing
grounded survives -> we never fabricate. `call_llm(system, user) -> str` is injected so tests
run offline. This core is shared with the batch crawler (Sub-project 2)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SYS = (
    "You extract answers from an official NJIT web page. Given the PAGE text and a student's "
    "QUESTION, return ONLY exact sentences COPIED VERBATIM from the page that answer the "
    "question. Do NOT paraphrase, summarize, combine, translate, or add anything not on the "
    'page. Respond with strict JSON: {"spans": ["<verbatim sentence>", ...]}. '
    'If the page does not answer the question, respond {"spans": []}.'
)
_MAX_PAGE_CHARS = 12000
_MIN_SPAN = 12
_MAX_SPANS = 6


@dataclass
class AnswerSpans:
    spans: list[str]
    source_url: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def build_extract_prompt(question: str, page_text: str) -> tuple[str, str]:
    user = f"QUESTION: {question}\n\nPAGE:\n{page_text[:_MAX_PAGE_CHARS]}"
    return _SYS, user


def ground_spans(llm_raw: str, page_text: str, source_url: str) -> "AnswerSpans | None":
    try:
        blob = llm_raw[llm_raw.index("{"): llm_raw.rindex("}") + 1]
        cand = json.loads(blob).get("spans") or []
    except (ValueError, json.JSONDecodeError):
        return None
    page_n = _norm(page_text)
    kept: list[str] = []
    seen: set[str] = set()
    for s in cand:
        if not isinstance(s, str):
            continue
        s = s.strip()
        sn = _norm(s)
        if len(sn) >= _MIN_SPAN and sn in page_n and sn not in seen:
            seen.add(sn)
            kept.append(s)
    return AnswerSpans(kept[:_MAX_SPANS], source_url) if kept else None


def answer_from_page(question: str, page_text: str, source_url: str, call_llm) -> "AnswerSpans | None":
    system, user = build_extract_prompt(question, page_text)
    try:
        raw = call_llm(system, user)
    except Exception:
        return None
    return ground_spans(raw or "", page_text, source_url)
