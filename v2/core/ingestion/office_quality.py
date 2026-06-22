"""Deterministic pre-ingest quality gate for office prose (no LLM). Drops nav/boilerplate
and near-empty chunks, and strips lines repeated across an office sub-tree (shared nav/footer).
spec §4.3 [RA5]."""
from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"\w+")


def is_low_quality(text: str, *, min_chars: int = 200, min_words: int = 40,
                   max_link_density: float = 0.5) -> bool:
    t = (text or "").strip()
    if len(t) < min_chars:
        return True
    words = _WORD.findall(t)
    if len(words) < min_words:
        return True
    # link/menu density proxy: a high ratio of short capitalised nav tokens
    short_caps = sum(1 for w in words if w[:1].isupper() and len(w) <= 12)
    if words and short_caps / len(words) > max_link_density and len(words) < 120:
        return True
    return False


def dedup_boilerplate(pages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove lines that repeat across >= half the pages (shared nav/footer)."""
    if len(pages) < 2:
        return pages
    counts: Counter[str] = Counter()
    for _url, text in pages:
        for line in {ln.strip() for ln in text.splitlines() if ln.strip()}:
            counts[line] += 1
    threshold = max(2, (len(pages) + 1) // 2)
    boiler = {ln for ln, n in counts.items() if n >= threshold}
    out: list[tuple[str, str]] = []
    for url, text in pages:
        kept = "\n".join(ln for ln in text.splitlines() if ln.strip() not in boiler)
        out.append((url, kept.strip()))
    return out
