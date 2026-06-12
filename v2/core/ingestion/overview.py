"""Generate a per-entity narrative overview ("short story"), grounded ONLY in the
verified facts we crawled.

This is the "first layer": a few-sentence synthesis of who a professor is and what
they work on, so a query like "tell me about Koutis" retrieves one coherent story
instead of stitching together dozens of publication fragments. The local LLM is
told to use ONLY the supplied facts and never invent — so the result is a faithful
compression of verified data, not outside knowledge.

The prompt assembly (``build_context`` / ``build_prompt``) is pure and unit-tested;
the LLM call is injected (``generate(rec, call_llm)``), so the runner supplies the
real Ollama call and tests supply a stub.

Note on publication sampling: an overview needs research THEMES, not every citation.
We feed the research statement + bio (the richest signals) plus a sample of
publication TITLES. This bounds the *generation input* (a summary compresses by
nature) — it does NOT cap the stored data; all publications remain their own items.
"""
from __future__ import annotations

import re

from v2.core.ingestion.entity import EntityRecord

# How many publication titles to feed the summarizer for theme extraction. Bounds
# the generation INPUT only (the overview is a summary); all pubs stay as items.
PUB_TITLE_SAMPLE = 30

SYSTEM = (
    "You write short, factual profiles of university faculty for a graduate-student "
    "assistant. You are given a set of FACTS about one professor. Write a concise "
    "overview (3-5 sentences) of who they are and what they research, using ONLY the "
    "facts provided. Do NOT invent awards, dates, titles, institutions, or claims "
    "that are not in the facts. Do not list every paper — synthesize the recurring "
    "research themes. Write in plain prose, third person, no markdown, no preamble — "
    "output only the overview."
)

_QUOTED = re.compile(r'"([^"]{8,200})"')


def _pub_title(citation: str) -> str:
    """The quoted title from a citation if present, else a leading snippet."""
    m = _QUOTED.search(citation)
    return m.group(1).strip() if m else citation[:120].strip()


def build_context(rec: EntityRecord) -> str:
    """Assemble the grounding facts block (pure)."""
    lines: list[str] = [f"Name: {rec.name}"]
    if rec.titles:
        lines.append(f"Title(s): {'; '.join(rec.titles)}")
    if rec.org:
        lines.append(f"Department: {rec.org}")
    if rec.research_statement.strip():
        lines.append(f"Research statement: {rec.research_statement.strip()}")
    if rec.research_areas:
        lines.append(f"Research areas: {'; '.join(rec.research_areas)}")
    if rec.bio.strip():
        lines.append(f"About: {rec.bio.strip()}")
    if rec.teaching:
        lines.append(f"Teaches: {'; '.join(rec.teaching)}")
    if rec.education:
        lines.append(f"Education: {'; '.join(rec.education)}")
    titles = [_pub_title(p.title) for p in rec.publications if p.title.strip()]
    if titles:
        sample = titles[:PUB_TITLE_SAMPLE]
        lines.append(f"Representative publication titles ({len(titles)} total, "
                     f"showing {len(sample)}):")
        lines += [f"  - {t}" for t in sample]
    return "\n".join(lines)


def build_prompt(rec: EntityRecord) -> tuple[str, str]:
    user = (f"FACTS about the professor:\n{build_context(rec)}\n\n"
            f"Write the overview now, using only these facts.")
    return SYSTEM, user


def generate(rec: EntityRecord, call_llm) -> str:
    """Produce the overview text. ``call_llm(system, user) -> str`` is injected so
    the LLM backend (Ollama) and tests are decoupled. Returns '' on empty output."""
    if not rec.name:
        return ""
    system, user = build_prompt(rec)
    text = (call_llm(system, user) or "").strip()
    # guard against a model that echoes a preamble label
    text = re.sub(r"^(Overview|Profile|Summary)\s*[:\-]\s*", "", text, flags=re.I).strip()
    return text
