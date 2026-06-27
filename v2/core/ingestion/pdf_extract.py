"""Mechanical PDF text extraction (provider-isolated behind pypdf).

Crawl-lane, mechanical-only: pypdf default extraction + text-preserving whitespace
normalization. NO OCR, NO rewriting, NO row reconstruction. Image-only / invalid PDFs
are skip-flagged (status), never faked. Dense numeric tables extract verbatim but with
degraded row boundaries -> table_degraded=True (serving layer adds source-link + warning).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

# image-heavy heuristic (validated on real NJIT PDFs 2026-06-27)
_MIN_CHARS_PER_PAGE = 200
_MIN_BYTES_PER_TEXT_CHAR = 800

# dense-numeric-table heuristic (validated on tuition.pdf 2026-06-27):
# A dense monetary table loses its column alignment after whitespace normalization.
# Two signals: (1) consecutive monetary values separated only by whitespace (adjacent
# table cells collapsed to "660.00 279.00"), (2) many money-shaped numbers overall.
_CONSEC_MONETARY = re.compile(r"\d[\d,]*\.\d{2}\s+\d[\d,]*\.\d{2}")  # adjacent monetary cells
_MANY_NUMBERS = re.compile(r"\d[\d,]*\.\d{2}")


@dataclass
class ExtractResult:
    text: str | None
    status: str           # ok | empty | image_heavy | mixed_low_text | invalid
    n_pages: int
    median_chars_per_page: int
    bytes_per_text_char: float
    table_degraded: bool
    reason: str


def _clean(text: str) -> str:
    """Text-preserving mechanical normalization: collapse ALL whitespace (incl. newlines) to
    single spaces. Never delete a character between word chars (would join wrapped words)."""
    return re.sub(r"\s+", " ", text).strip()


def _read_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return Path(source).read_bytes()


def extract_pdf_text(source) -> ExtractResult:
    raw = _read_bytes(source)
    size = len(raw)
    if raw[:5] != b"%PDF-":
        return ExtractResult(None, "invalid", 0, 0, 0.0, False, "missing %PDF- header")
    import io
    try:
        reader = PdfReader(io.BytesIO(raw))
        page_texts = [(p.extract_text() or "") for p in reader.pages]
    except (PyPdfError, Exception) as e:          # pypdf raises various; treat all as invalid
        return ExtractResult(None, "invalid", 0, 0, 0.0, False, f"{type(e).__name__}: {e}")

    n = len(page_texts)
    per_page_chars = [len(t.strip()) for t in page_texts]
    total_chars = sum(per_page_chars)
    median_cpp = int(statistics.median(per_page_chars)) if per_page_chars else 0
    bpc = size / total_chars if total_chars else float("inf")
    near_empty = sum(1 for c in per_page_chars if c < 20)

    if total_chars == 0:
        return ExtractResult(None, "empty", n, 0, bpc, False, "no extractable text")
    if median_cpp < _MIN_CHARS_PER_PAGE and bpc > _MIN_BYTES_PER_TEXT_CHAR:
        return ExtractResult(None, "image_heavy", n, median_cpp, round(bpc, 1), False,
                             "low text + high bytes/char -> likely scanned/screenshots")

    text = _clean("\n".join(page_texts))
    degraded = bool(_CONSEC_MONETARY.search(text)) and len(_MANY_NUMBERS.findall(text)) >= 5
    status = "ok"
    reason = ""
    if n > 1 and near_empty >= 1 and near_empty < n:
        status, reason = "mixed_low_text", f"{near_empty}/{n} pages near-empty (review)"
    return ExtractResult(text, status, n, median_cpp, round(bpc, 1), degraded, reason)
