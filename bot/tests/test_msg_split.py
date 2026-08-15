"""Tests for bot/core/msg_split.py — platform message-length splitting.

Written test-first against the design spec
`docs/superpowers/specs/2026-08-14-telegram-message-split-design.md` (v2, post-review).

The bug these pin: "who is Taro Narahara" produced a ~7,100-char answer, Telegram's cap is
4,096, the send 400'd, the except-fallback re-sent the SAME oversized text and 400'd too, and
the user got complete silence.
"""
from __future__ import annotations

import html
import re
import time
from html.parser import HTMLParser

import pytest

from bot.core.msg_split import (
    MASKED_LINK_RE,
    split_for_telegram,
    split_plain,
    utf16_len,
)

TG_LIMIT = 4096


# ── A stand-in for telegram_connector._tg_html ───────────────────────────────
# Kept in sync with the real renderer's escape-then-tag order. Imported from the
# connector in the integration tests; duplicated here so the unit tests don't need
# the telegram package installed.
def _render(text: str) -> str:
    t = html.escape(text or "", quote=False)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t, flags=re.S)
    t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t, flags=re.S)
    return t


class _NestingChecker(HTMLParser):
    """Assert well-formed NESTING, not merely equal tag counts.

    Spec finding SE-2: `<b>bold <i>ital</b> rest</i>` has equal open/close counts for both
    tags and would pass a counting test, but Telegram rejects it with "Can't parse entities".
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"close </{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"close </{tag}> while <{self.stack[-1]}> is innermost")
        else:
            self.stack.pop()


def assert_well_nested(rendered: str) -> None:
    checker = _NestingChecker()
    checker.feed(rendered)
    checker.close()
    assert not checker.errors, f"malformed nesting: {checker.errors}"
    assert not checker.stack, f"unclosed tags: {checker.stack}"


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


# ── 1. no-op path ────────────────────────────────────────────────────────────
def test_short_text_returns_single_identical_chunk():
    text = "Taro Narahara is an Associate Professor at NJIT."
    assert split_plain(text, TG_LIMIT) == [text]


def test_empty_text_returns_no_chunks():
    assert split_plain("", TG_LIMIT) == []
    assert split_plain("   \n\n  ", TG_LIMIT) == []


# ── 2. the real answer size, measured on BOTH plain and rendered ─────────────
def test_long_answer_every_chunk_fits_plain_and_rendered():
    """v1's test checked only PLAIN length and would have passed the broken algorithm."""
    para = "Taro Narahara — Associate Professor, New Jersey School of Architecture. "
    text = "\n\n".join(para * 3 for _ in range(40))  # ~8.6k chars
    assert utf16_len(text) > TG_LIMIT

    chunks = split_for_telegram(text, TG_LIMIT, _render)

    assert len(chunks) >= 2
    for c in chunks:
        assert utf16_len(c) <= TG_LIMIT
        assert utf16_len(_render(c)) <= TG_LIMIT
    # content invariant: no non-whitespace character is lost
    assert strip_ws("".join(chunks)) == strip_ws(text)


# ── 3. THE regression: the reviewer's counter-example ────────────────────────
def test_ampersand_heavy_text_stays_under_limit_when_rendered():
    """Spec SE-1. `&` escapes to `&amp;` (1 -> 5 UTF-16 units).

    The v1 algorithm windowed in PLAIN space while budgeting in RENDERED space and produced
    chunks of 19,500 units — 4.7x over the cap — while every v1 test passed.
    """
    text = "&" * 4000
    chunks = split_for_telegram(text, TG_LIMIT, _render)
    for c in chunks:
        assert utf16_len(_render(c)) <= TG_LIMIT, (
            f"rendered chunk is {utf16_len(_render(c))} units — this is the v1 bug"
        )
    assert strip_ws("".join(chunks)) == strip_ws(text)


def test_mixed_entity_prose_stays_under_limit_when_rendered():
    """Real corpus shape: prose containing & and < from NJIT source pages."""
    text = "\n\n".join("Research & Development <b>bold</b> " * 60 for _ in range(12))
    chunks = split_for_telegram(text, TG_LIMIT, _render)
    for c in chunks:
        assert utf16_len(_render(c)) <= TG_LIMIT


# ── 4. UTF-16 counting ───────────────────────────────────────────────────────
def test_utf16_len_counts_emoji_as_surrogate_pairs():
    assert utf16_len("a") == 1
    assert utf16_len("🎓") == 2          # astral plane -> surrogate pair
    assert utf16_len("🔗 Source") == 9   # 2 + 7
    assert utf16_len("é") == 1           # BMP -> 1


def test_emoji_heavy_text_respects_utf16_budget():
    """A char-counting implementation passes this input while Telegram rejects it."""
    limit = 100
    text = "🎓" * 80  # 80 chars, 160 UTF-16 units
    chunks = split_plain(text, limit)
    for c in chunks:
        assert utf16_len(c) <= limit
    assert len(chunks) >= 2


# ── 5. boundary ladder + termination ─────────────────────────────────────────
def test_prefers_paragraph_break_past_the_halfway_mark():
    """A boundary is only taken past hi//2, so chunks stay reasonably full rather than
    breaking at the first available newline."""
    limit = 60
    head = "alpha bravo charlie delta echo foxtrot"  # 37 chars — past 60//2
    text = head + "\n\n" + "golf hotel india juliet kilo lima mike november oscar"
    chunks = split_plain(text, limit)
    assert chunks[0] == head  # cut at the paragraph break
    for c in chunks:
        assert utf16_len(c) <= limit


def test_early_paragraph_break_is_rejected_in_favor_of_a_fuller_chunk():
    limit = 60
    text = "alpha\n\n" + "bravo charlie delta echo foxtrot golf hotel india juliet"
    chunks = split_plain(text, limit)
    assert chunks[0] != "alpha", "a break at index 5 of a 60-unit budget wastes the chunk"
    for c in chunks:
        assert utf16_len(c) <= limit


def test_falls_back_to_line_then_space():
    limit = 40
    # no paragraph break at all — must use \n, then a space
    text = "alpha bravo charlie delta echo\nfoxtrot golf hotel india juliet kilo"
    chunks = split_plain(text, limit)
    assert chunks[0] == "alpha bravo charlie delta echo"
    for c in chunks:
        assert utf16_len(c) <= limit


def test_single_unbroken_token_hard_cuts_and_terminates():
    limit = 100
    text = "x" * 5000  # no paragraph, no line, no space anywhere
    start = time.monotonic()
    chunks = split_plain(text, limit)
    assert time.monotonic() - start < 5.0, "did not terminate promptly"
    assert len(chunks) >= 50
    for c in chunks:
        assert 0 < utf16_len(c) <= limit
    assert "".join(chunks) == text


def test_makes_progress_on_a_single_oversized_character_run():
    """The >=1-char floor: worst single char is `&` (5 rendered units), far under any budget."""
    chunks = split_for_telegram("&" * 50, 10, _render)
    assert all(c for c in chunks)
    assert strip_ws("".join(chunks)) == "&" * 50


# ── 6. never emit an empty chunk (would be Telegram 400 "message text is empty") ──
def test_never_emits_empty_chunks():
    limit = 40
    text = "alpha\n\n\n\n\n\nbravo" + ("\n" * 50) + "charlie " * 30
    for c in split_plain(text, limit):
        assert c.strip(), "empty chunk would 400 as 'message text is empty'"


# ── 7. masked-link atomicity (Fable F-1) ─────────────────────────────────────
def test_masked_link_is_never_cut_in_half():
    """A cut inside [label](url) exposes a raw half-URL. Scholar links from
    deterministic_suffix sit at the END of long answers — exactly where cuts land."""
    link = "[Google Scholar](https://scholar.google.com/citations?hl=en&user=RRVZtWgAAAAJ)"
    limit = 100
    # place the link so a naive cut lands in its middle
    text = ("a" * 60) + " " + link + " " + ("b" * 200)
    chunks = split_plain(text, limit)

    for c in chunks:
        # no chunk may contain a dangling half of the link
        assert not (c.count("[") != c.count("]")), f"split label: {c!r}"
        assert "scholar.google.com" not in c or link in c, f"split URL: {c!r}"
    assert link in "".join(chunks)


def test_masked_link_longer_than_budget_still_makes_progress():
    """Degenerate: the link alone exceeds the budget — must hard cut, not loop forever."""
    link = "[x](https://example.com/" + ("y" * 300) + ")"
    chunks = split_plain(link, 50)
    assert len(chunks) >= 2
    assert all(c for c in chunks)


# ── 8. tag balance, checked by NESTING not counting ──────────────────────────
def test_every_rendered_chunk_is_well_nested():
    text = "\n\n".join(
        f"**Bold {i}** and *italic {i}* with [link {i}](https://njit.edu/{i}) & more text "
        * 20
        for i in range(10)
    )
    for c in split_for_telegram(text, TG_LIMIT, _render):
        assert_well_nested(_render(c))


def test_no_chunk_ends_mid_entity():
    text = "&" * 3000 + "\n\n" + "&" * 3000
    for c in split_for_telegram(text, TG_LIMIT, _render):
        rendered = _render(c)
        # a truncated "&am" / "&amp" would leave a & not followed by a complete entity
        assert not re.search(r"&[a-z]{0,3}$", rendered), f"truncated entity: {rendered[-10:]!r}"


# ── 9. perf: no O(n^2) render-in-the-loop ────────────────────────────────────
def test_splitting_a_real_sized_answer_is_fast():
    """v1's implied char-granularity render-in-loop measured 1.07s of pure CPU, and PTB's
    max_concurrent_updates=1 default means that is 1.07s answering nobody."""
    text = ("Taro Narahara research statement paragraph. " * 20 + "\n\n") * 8  # ~7.1k
    assert utf16_len(text) > 7000
    start = time.monotonic()
    split_for_telegram(text, TG_LIMIT, _render)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"splitting took {elapsed:.3f}s — regressed to render-in-loop?"


# ── misc guards ──────────────────────────────────────────────────────────────
def test_masked_link_regex_matches_our_suffix_shape():
    suffix = "🎓 [Google Scholar](https://scholar.google.com/citations?hl=en&user=RRV) · 🌐 [Website](https://www.narahara.net/)"
    assert len(MASKED_LINK_RE.findall(suffix)) == 2


@pytest.mark.parametrize("limit", [10, 37, 100, 512, 4096])
def test_invariants_hold_across_budgets(limit):
    text = "\n\n".join("Paragraph & text with [a link](https://njit.edu/x) inside. " * 5
                       for _ in range(20))
    chunks = split_for_telegram(text, limit, _render)
    for c in chunks:
        assert c.strip()
        assert utf16_len(c) <= limit or len(c) <= 8  # floor case
    assert strip_ws("".join(chunks)) == strip_ws(text)


# ── hard_limit vs plain budget (regression: over-splitting) ──────────────────
def test_net_does_not_fire_when_render_fits_the_hard_limit():
    """The rendered form may legitimately exceed the PLAIN budget while still fitting the cap.

    Checking rendered size against the smaller plain budget split the live Narahara answer
    into 4 messages instead of 2.
    """
    text = "\n\n".join("Prose with <b>source tags</b> and & entities. " * 40 for _ in range(2))
    budget, hard = 3755, 4096
    with_hard = split_for_telegram(text, budget, _render, hard_limit=hard)
    without = split_for_telegram(text, budget, _render)
    assert len(with_hard) <= len(without), "hard_limit must not over-split"
    for c in with_hard:
        assert utf16_len(_render(c)) <= hard


def test_net_still_fires_when_render_exceeds_the_hard_limit():
    """The net must remain live — this is the &-heavy case it exists for."""
    chunks = split_for_telegram("&" * 4000, 3900, _render, hard_limit=TG_LIMIT)
    assert len(chunks) > 1
    for c in chunks:
        assert utf16_len(_render(c)) <= TG_LIMIT
