"""Shared answer-footer rendering — ONE source of truth for both connectors.

Every answer gets a brand line ("💡 GSA Gateway · Kavosh v2.0 · <provenance>"). The source is
shown as a CLICKABLE "Source" (never the raw URL): Telegram renders an <a> tag, Discord a
masked markdown link in the embed description (its footer can't hold links). A friendly-name
source (KB answers, e.g. "GSA FAQ") stays a plain "Source: <name>" label.
"""
from __future__ import annotations

import html
from typing import Optional

BRAND = "💡 GSA Gateway · Kavosh v2.0"


def provenance_label(*, used_ai: bool, is_live: bool) -> Optional[str]:
    if is_live:
        return "Live verbatim from njit.edu"
    if used_ai:
        return "AI-generated from official GSA docs"
    return None


def _is_url(s: Optional[str]) -> bool:
    return bool(s) and s.startswith(("http://", "https://"))


def _brand_line(*, used_ai: bool, is_live: bool) -> str:
    prov = provenance_label(used_ai=used_ai, is_live=is_live)
    return f"{BRAND} · {prov}" if prov else BRAND


def telegram_footer_html(*, source_note: Optional[str], used_ai: bool, is_live: bool) -> str:
    """The footer block appended to a Telegram (HTML parse-mode) answer. Built as raw HTML so
    the <a> link survives — do NOT pass this through _tg_html (which would escape the tag)."""
    lines: list[str] = []
    if _is_url(source_note):
        lines.append(f'🔗 <a href="{html.escape(source_note, quote=True)}">Source</a>')
    elif source_note:
        lines.append(f"<i>Source: {html.escape(source_note)}</i>")
    lines.append(f"<i>{html.escape(_brand_line(used_ai=used_ai, is_live=is_live))}</i>")
    return "\n\n" + "\n".join(lines)


def discord_source_link(source_note: Optional[str]) -> Optional[str]:
    """A masked markdown link line for the embed DESCRIPTION when the source is a URL."""
    if _is_url(source_note):
        return f"🔗 [Source]({source_note})"
    return None


def discord_footer_text(*, source_note: Optional[str], used_ai: bool, is_live: bool) -> str:
    """The embed footer text: brand · [Source: name] · provenance. A URL source is NOT put
    here (footers can't link) — it goes in the description via discord_source_link()."""
    parts = [BRAND]
    if source_note and not _is_url(source_note):
        parts.append(f"Source: {source_note}")
    prov = provenance_label(used_ai=used_ai, is_live=is_live)
    if prov:
        parts.append(prov)
    return " · ".join(parts)
