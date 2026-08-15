"""Message splitting for platform send limits — ONE helper for every connector.

Why this exists: a long answer (e.g. the ~7,100-char entity card for a faculty member with a
full NJIT profile) exceeded Telegram's 4,096-char cap, the send 400'd with "Message is too
long", the except-fallback re-sent the SAME oversized text and 400'd too, and the user got
complete silence. See
`docs/superpowers/specs/2026-08-14-telegram-message-split-design.md`.

Design note — we split the PLAIN markdown and let the caller render each chunk separately,
rather than splitting already-rendered HTML. Rendering per chunk makes every chunk
tag-balanced by construction, with no hand-rolled HTML scanner in the serving path.

Budget note — Telegram's Bot API specifies sendMessage.text as "1-4096 characters AFTER
entities parsing": tags are stripped and entities decoded before counting. Rendering only ever
shortens the parsed result (`&amp;`->`&`, `**b**`->`b`, `[l](url)`->`l`; nothing is added), so
budgeting on the PLAIN length is already sufficient — and tighter than budgeting on the
rendered length. `split_for_telegram` adds a post-condition net for the stricter reading.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

# A masked markdown link must never be cut in half: a raw half-URL is materially worse than a
# stray asterisk, and the Scholar links appended by `deterministic_suffix` sit at the END of
# long answers — exactly where cuts land.
MASKED_LINK_RE = re.compile(r"\[[^\]]+\]\([^)\s]+\)")

# Guarantees progress (and therefore termination) when no boundary is usable. The worst single
# character is `&` -> `&amp;` = 5 rendered units, far under any real budget.
_MIN_PROGRESS = 1

# Below this budget, stop trying to shrink further and accept the chunk — prevents unbounded
# recursion on pathological input.
_BUDGET_FLOOR = 8


def utf16_len(s: str) -> int:
    """Length in UTF-16 code units — how Telegram counts, not how Python counts.

    An emoji (🎓 🔗 💡 — all present in our footers and Scholar suffix) is a surrogate pair
    and counts as 2. A char-counting implementation silently under-measures these.
    """
    return len(s.encode("utf-16-le")) // 2


def _max_prefix_chars(s: str, limit: int) -> int:
    """Largest k with utf16_len(s[:k]) <= limit. Binary search — utf16_len is monotonic in
    the prefix, and utf16_len(s) >= len(s), so k is bounded by `limit` characters."""
    lo, hi = 0, min(len(s), limit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(s[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _avoid_atomic_span(text: str, cut: int, pattern: Optional[re.Pattern]) -> int:
    """If `cut` falls strictly inside an atomic span, back off to the span's start.

    Returns 0 when the span starts at 0 (i.e. the span alone exceeds the budget) so the caller
    falls through to a hard cut rather than making no progress.
    """
    if pattern is None or cut <= 0:
        return cut
    for m in pattern.finditer(text):
        if m.start() >= cut:
            break
        if m.start() < cut < m.end():
            return m.start()
    return cut


def split_plain(
    text: str,
    limit: int,
    *,
    atomic_spans: Optional[re.Pattern] = MASKED_LINK_RE,
) -> list[str]:
    """Split PLAIN markdown into chunks of at most `limit` UTF-16 code units.

    Boundary ladder: paragraph (\\n\\n) -> line (\\n) -> space -> hard cut. A boundary is only
    taken if it lands past the halfway mark, so chunks stay reasonably full.

    Invariants (all pinned by bot/tests/test_msg_split.py):
      * every chunk is non-empty and <= `limit` UTF-16 units
      * no non-whitespace character is lost
      * never cuts inside an `atomic_spans` match (unless the span alone exceeds the budget)
      * always makes >= 1 character of progress, so it provably terminates
    """
    text = (text or "").strip()
    if not text:
        return []
    if utf16_len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while utf16_len(remaining) > limit:
        hi = _max_prefix_chars(remaining, limit)
        if hi < _MIN_PROGRESS:
            hi = _MIN_PROGRESS
        window = remaining[:hi]

        cut = window.rfind("\n\n")
        if cut < hi // 2:
            cut = window.rfind("\n")
        if cut < hi // 2:
            cut = window.rfind(" ")

        cut = _avoid_atomic_span(remaining, cut, atomic_spans)

        if cut <= 0:
            cut = hi  # hard cut — no usable boundary (or an atomic span filling the window)
        cut = max(cut, _MIN_PROGRESS)

        piece = remaining[:cut].strip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].strip()
        if not remaining:
            break

    if remaining:
        chunks.append(remaining)
    return chunks


def split_for_telegram(
    text: str,
    limit: int,
    render: Callable[[str], str],
    *,
    hard_limit: Optional[int] = None,
    atomic_spans: Optional[re.Pattern] = MASKED_LINK_RE,
) -> list[str]:
    """`split_plain` plus a POST-CONDITION safety net on the RENDERED size.

    Budgeting on plain length is sufficient under the documented "after entities parsing"
    behavior. This net makes the module correct under the stricter reading too (limit applied
    to the raw markup we send): any chunk whose rendered form still exceeds `limit` is
    re-split at a halved budget until it fits or hits the floor.

    `limit` is the PLAIN-space budget (deliberately below the cap, to leave room for a footer
    and a trailing partial word). `hard_limit` is the actual cap the RENDERED payload must
    respect, and defaults to `limit`.

    Keeping them separate matters: budgeting plain at 3,755 while a chunk renders to 3,854 is
    perfectly fine when the real cap is 4,096. Checking the rendered size against the smaller
    plain budget made the net fire on a chunk that already fit, splitting the live Narahara
    answer into 4 messages instead of 2.
    """
    hard = limit if hard_limit is None else hard_limit
    chunks = split_plain(text, limit, atomic_spans=atomic_spans)
    out: list[str] = []
    for chunk in chunks:
        out.extend(_enforce_rendered(chunk, hard, render, limit, atomic_spans))
    return out


def _enforce_rendered(
    chunk: str,
    limit: int,
    render: Callable[[str], str],
    budget: int,
    atomic_spans: Optional[re.Pattern],
) -> list[str]:
    if utf16_len(render(chunk)) <= limit:
        return [chunk]
    if budget <= _BUDGET_FLOOR:
        return [chunk]  # cannot shrink further; caller's plain-text fallback is the backstop
    budget = max(_BUDGET_FLOOR, budget // 2)
    parts = split_plain(chunk, budget, atomic_spans=atomic_spans)
    if len(parts) <= 1:
        return [chunk]
    out: list[str] = []
    for part in parts:
        out.extend(_enforce_rendered(part, limit, render, budget, atomic_spans))
    return out
