"""Keep-fullest decision for two captures of the same canonical prose URL (day-1 rebuild Task 3).

"Fullest" = most REAL content, NOT raw byte length and NOT type-token ratio (TTR is length-biased
toward the SHORTER capture — the opposite of the goal; rev2-RAG#1). Since every engine in the rebuild
runs the same `extract_prose` (main-region extraction), the stored content is already boilerplate-light,
so a whitespace token count is a sound proxy for real-content volume. The dominant rule is TYPE: a
marketing `webpage` row must never supersede a substantive `policy/news/event` row (the live
graduate-admissions trap; spec §4.4, SE#2/#4).

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §4.4
"""
from __future__ import annotations

# Substantive prose types: a 'webpage' (marketing/landing bucket) never supersedes these.
_SUBSTANTIVE = frozenset({"policy", "news", "event"})


def prose_quality_len(content: str) -> int:
    """Real-content volume = whitespace-delimited token count of the (already main-region-extracted)
    content. Not raw length, not unique-token ratio."""
    return len((content or "").split())


def keep_better(a_content: str, a_type: str, b_content: str, b_type: str) -> bool:
    """True iff capture A should WIN over capture B for the same canonical URL.
    Type dominates: a 'webpage' never beats a substantive type. Within the same tier, more real
    content wins. A strict tie returns False (caller breaks ties by recency → newest wins on equal)."""
    a_sub = a_type in _SUBSTANTIVE
    b_sub = b_type in _SUBSTANTIVE
    if a_sub != b_sub:
        return a_sub                      # substantive beats webpage; webpage never beats substantive
    return prose_quality_len(a_content) > prose_quality_len(b_content)
