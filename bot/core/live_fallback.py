"""KB-miss -> live njit.edu search -> fetch top page -> extractive grounded answer + link.

Returns None if search/fetch/extract yields nothing grounded (caller keeps today's decline).
Collaborators are injected: `search_fn(query)->[url]` and `fetch_fn(url)->(final_url, html,
status)` are sync (run in threads); `generate(system, user)` is an ASYNC callable awaited on
the bot's event loop, so it can reuse the existing Ollama aiohttp session safely (no nested
event loop). Grounding is the pure `ground_spans` from the shared core — only verbatim spans
literally present on the page survive."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from v2.core.ingestion.grounded_extract import build_extract_prompt, ground_spans
from v2.core.ingestion.web_crawler import clean_text

logger = logging.getLogger(__name__)


@dataclass
class LiveAnswer:
    text: str
    source_url: str


@dataclass
class LiveLinks:
    """A1 off-target degrade: the relevance-gate found no page that ANSWERS the question, so hand
    back the top njit.edu links honestly instead of a confident wrong extract (zero LLM in this path —
    URLs are verbatim from Brave). Rendered by the caller as 'closest pages: 1)…2)…3)…'."""
    urls: list[str]


def _format(spans: list[str], source_url: str) -> str:
    # Source is carried on LiveAnswer.source_url and rendered ONCE by the caller (source_note /
    # platform footer / the offer-tap reply) — do NOT embed it here, or it prints twice.
    body = " ".join(spans)
    return f"🌐 Live from NJIT's website (fetched live): {body}"


async def maybe_answer_live(question, *, search_fn, fetch_fn, generate,
                            relevance_ok=None, degrade_links: bool = False, max_pages: int = 3):
    """KB-miss → njit.edu extractive answer. `relevance_ok(question, spans)->bool` (async, optional):
    an off-target page whose verbatim spans don't ANSWER the question is skipped (A1). On no
    grounded+relevant page: `degrade_links` → LiveLinks(top-3 URLs); else None (nothing found)."""
    try:
        urls = await asyncio.to_thread(search_fn, question)
    except Exception:
        logger.warning("live search failed", exc_info=True)
        return None
    for url in (urls or [])[:max_pages]:
        try:
            final_url, html, status = await asyncio.to_thread(fetch_fn, url)
        except Exception:
            continue
        if not html or status != "ok":  # http_fetch returns status "ok" on success, else ""/error
            continue
        page_text = clean_text(html)
        system, user = build_extract_prompt(question, page_text)
        try:
            raw = await generate(system, user)
        except Exception:
            logger.warning("live LLM extract failed", exc_info=True)
            continue
        ans = ground_spans(raw or "", page_text, final_url or url)
        if ans is None:
            continue
        if relevance_ok is not None:
            try:
                if not await relevance_ok(question, ans.spans):
                    continue                       # grounded but OFF-TARGET — try the next page
            except Exception:
                logger.warning("live relevance gate faulted — keeping (never-withhold)", exc_info=True)
        return LiveAnswer(text=_format(ans.spans, ans.source_url), source_url=ans.source_url)
    # no page yielded a grounded + relevant answer
    if degrade_links and urls:
        return LiveLinks(urls=list(urls[:3]))
    return None
