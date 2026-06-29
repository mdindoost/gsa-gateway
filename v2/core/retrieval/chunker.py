"""Structure-aware chunker for parent-document retrieval.

Splits a parent item's plain text into token-bounded, overlapping, VERBATIM chunks
sized by the active model descriptor (working_size tokens, overlap tokens), measured
with the model's OWN tokenizer. Each chunk is a literal substring of the parent (cut
only at token boundaries, snapped back to a sentence end where possible) so nothing is
reworded — the full parent is still what gets served; chunks exist only to be embedded.

No semantic/LLM boundary detection (research: not worth the cost). A short item yields
exactly one chunk identical to its text.
"""
from __future__ import annotations

from v2.core.retrieval.model_descriptor import ModelDescriptor

_SENT_END = ".!?\n"


def is_blank(text: str) -> bool:
    """True iff `text` has no content the chunker would chunk.

    The single source of truth for "blank" — both ``chunk_text`` (which yields
    zero chunks) and the chunk invariant's coverage check use it, so an item is
    considered empty in EXACTLY the same way in both places (Python ``str.strip``,
    i.e. Unicode whitespace — not just ASCII)."""
    return not (text or "").strip()


def _snap_back(text: str, offsets, start: int, end: int) -> int:
    """Largest token index e in (start, end] whose last char ends a sentence; else `end`.

    Only looks back within the last quarter of the window so a snap never produces a
    tiny chunk.
    """
    floor = max(start + 1, end - (end - start) // 4)
    for e in range(end, floor - 1, -1):
        last_char = text[offsets[e - 1][1] - 1]
        if last_char in _SENT_END:
            return e
    return end


def chunk_text(text: str, descriptor: ModelDescriptor) -> list[str]:
    if is_blank(text):
        return []
    text = text.strip()
    enc = descriptor.tokenizer.encode(text, add_special_tokens=False)
    offsets = enc.offsets
    n = len(enc.ids)
    ws, ov = descriptor.working_size, descriptor.overlap
    if n <= ws:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + ws, n)
        if end < n:
            end = _snap_back(text, offsets, start, end)
        chunks.append(text[offsets[start][0]:offsets[end - 1][1]])
        if end >= n:
            break
        start = max(end - ov, start + 1)   # advance with overlap; guarantee progress
    return chunks
