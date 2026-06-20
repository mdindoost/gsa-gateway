"""Shared answer-footer rendering: brand line + provenance + clickable 'Source' link.

One source of truth for both connectors so they can't drift. The raw URL is never shown —
it's hidden behind a clickable 'Source' (Telegram <a>, Discord masked link). A friendly-name
source (KB answers) stays a plain 'Source: <name>' label.
"""
from bot.core.answer_render import (
    BRAND,
    provenance_label,
    telegram_footer_html,
    discord_footer_text,
    discord_source_link,
)


# ── provenance ────────────────────────────────────────────────────────────────
def test_provenance_live_beats_used_ai():
    assert provenance_label(used_ai=True, is_live=True) == "Live verbatim from njit.edu"


def test_provenance_used_ai_only():
    assert provenance_label(used_ai=True, is_live=False) == "AI-generated from official GSA docs"


def test_provenance_none_when_neither():
    assert provenance_label(used_ai=False, is_live=False) is None


# ── Telegram footer (HTML) ──────────────────────────────────────────────────
def test_telegram_url_source_is_hyperlinked_not_raw():
    out = telegram_footer_html(source_note="https://www.njit.edu/parking", used_ai=True, is_live=True)
    assert '<a href="https://www.njit.edu/parking">Source</a>' in out
    assert "https://www.njit.edu/parking</" not in out  # url not shown as visible text
    assert BRAND in out
    assert "Live verbatim from njit.edu" in out


def test_telegram_named_source_is_plain_label():
    out = telegram_footer_html(source_note="GSA FAQ", used_ai=True, is_live=False)
    assert "Source: GSA FAQ" in out
    assert "<a href" not in out
    assert "AI-generated from official GSA docs" in out


def test_telegram_brand_always_present_even_without_source():
    out = telegram_footer_html(source_note=None, used_ai=False, is_live=False)
    assert BRAND in out


# ── Discord ───────────────────────────────────────────────────────────────────
def test_discord_url_source_becomes_masked_link():
    assert discord_source_link("https://www.njit.edu/parking") == "🔗 [Source](https://www.njit.edu/parking)"


def test_discord_named_source_has_no_masked_link():
    assert discord_source_link("GSA FAQ") is None


def test_discord_footer_url_source_keeps_brand_and_provenance_not_url():
    txt = discord_footer_text(source_note="https://www.njit.edu/parking", used_ai=True, is_live=True)
    assert txt.startswith(BRAND)
    assert "Live verbatim from njit.edu" in txt
    assert "njit.edu/parking" not in txt   # the URL lives in the description link, not the footer


def test_discord_footer_named_source_kept_in_footer():
    txt = discord_footer_text(source_note="GSA FAQ", used_ai=True, is_live=False)
    assert "Source: GSA FAQ" in txt
    assert "AI-generated from official GSA docs" in txt
