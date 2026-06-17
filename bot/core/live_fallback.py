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


def _format(spans: list[str], source_url: str) -> str:
    body = " ".join(spans)
    return f"From NJIT's website: {body}\n\nSource: {source_url}"


async def maybe_answer_live(question, *, search_fn, fetch_fn, generate, max_pages: int = 2):
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
        if not html or not str(status).startswith("2"):
            continue
        page_text = clean_text(html)
        system, user = build_extract_prompt(question, page_text)
        try:
            raw = await generate(system, user)
        except Exception:
            logger.warning("live LLM extract failed", exc_info=True)
            continue
        ans = ground_spans(raw or "", page_text, final_url or url)
        if ans is not None:
            return LiveAnswer(text=_format(ans.spans, ans.source_url), source_url=ans.source_url)
    return None
