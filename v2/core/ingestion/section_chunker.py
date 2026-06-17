"""Structure-aware markdown chunker for the structured policy docs (GSA constitution,
club bylaws, travel packet, PhD program pages).

The token-window chunker (``split_text_by_tokens``) merges adjacent sections, so distinct
facts (an officer's duties, the Advisors clause, the Prizes rule) end up in one chunk and
compete at retrieval time. This chunker splits on markdown headings so each
section/subsection is its own chunk, prefixed with its heading path for context
("Section II: Executive Board > Vice President of Finance: …"). Over-long sections are
sub-split by paragraph while keeping the heading prefix.
"""
from __future__ import annotations

import re
from pathlib import Path

from bot.services.chunker import DocumentChunker

_CHUNKER = DocumentChunker(Path(__file__).resolve().parents[3] / "bot" / "data")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _ntokens(text: str) -> int:
    return _CHUNKER.count_tokens(text)


def _normalize_ws(text: str) -> str:
    """Collapse source hard-wrap newlines (and list-item line breaks) within a paragraph to
    single spaces, keeping blank-line paragraph breaks. Without this, a fact wrapped across
    two source lines carries an embedded newline and won't match as continuous prose."""
    paras = re.split(r"\n\s*\n", text)
    return "\n\n".join(re.sub(r"[ \t]*\n[ \t]*", " ", p).strip()
                       for p in paras if p.strip())


def _split_body(body: str, budget: int) -> list[str]:
    """Greedily pack paragraphs/list-items into <=budget-token pieces; fall back to the
    token splitter for a single oversized paragraph."""
    budget = max(40, budget)
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    cur = ""
    for p in paras:
        if _ntokens(p) > budget:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(_CHUNKER.split_text_by_tokens(p))
            continue
        candidate = f"{cur}\n\n{p}".strip()
        if cur and _ntokens(candidate) > budget:
            out.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


def chunk_markdown(text: str, max_tokens: int = 320) -> list[str]:
    """Split markdown into structure-aware chunks. Each heading starts a new block; the
    block's body runs until the next heading. Every chunk is prefixed with its heading path
    (ancestors of higher level) so a bare subsection chunk keeps its context."""
    lines = text.split("\n")
    blocks: list[tuple[int, str, list[str]]] = []  # (level, heading, body_lines)
    pre: list[str] = []
    cur: tuple[int, str, list[str]] | None = None
    for line in lines:
        m = _HEADING.match(line)
        if m:
            if cur:
                blocks.append(cur)
            cur = (len(m.group(1)), m.group(2).strip(), [])
        elif cur:
            cur[2].append(line)
        else:
            pre.append(line)
    if cur:
        blocks.append(cur)

    chunks: list[str] = []
    pre_text = _normalize_ws("\n".join(pre).strip())
    if pre_text:
        # Heading-less preamble (or an entire heading-less doc) still respects the budget.
        chunks.extend(_split_body(pre_text, max_tokens) if _ntokens(pre_text) > max_tokens
                      else [pre_text])

    stack: list[tuple[int, str]] = []  # (level, heading) ancestors
    for level, heading, body_lines in blocks:
        while stack and stack[-1][0] >= level:
            stack.pop()
        path = " > ".join(h for _, h in (*stack, (level, heading)))
        stack.append((level, heading))
        body = _normalize_ws("\n".join(body_lines).strip())
        if not body:
            continue  # heading whose content lives in its subsections
        full = f"{path}\n{body}"
        if _ntokens(full) <= max_tokens:
            chunks.append(full)
        else:
            for sub in _split_body(body, max_tokens - _ntokens(path) - 2):
                chunks.append(f"{path}\n{sub}")
    return [c for c in chunks if c.strip()]
