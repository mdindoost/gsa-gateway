"""TDD tests for Task 6: PDF ingestion wiring (crawl-lane).

Covers:
  - ingest_pdf_pages: tuition (ok, degraded), calendar (ok, clean), image_heavy (skip),
    None-returning fetch (skip), idempotency.
  - _build_context_block: degraded safeguard line present/absent.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURES = REPO / "v2" / "tests" / "fixtures" / "pdf"
TUITION_PDF = FIXTURES / "tuition.pdf"
CALENDAR_PDF = FIXTURES / "calendar.pdf"
IMAGE_HEAVY_PDF = FIXTURES / "image_heavy_synth.pdf"


def _conn():
    from v2.core.database.schema import create_all
    from v2.core.graph.orgs import ensure_org
    c = create_all(":memory:")
    ensure_org(c, "njit", "NJIT", None, type="university")
    ensure_org(c, "ywcc", "YWCC", "njit", type="college")
    return c


# ── ingest_pdf_pages tests ────────────────────────────────────────────────────

def test_tuition_pdf_creates_degraded_row():
    """tuition.pdf is a dense monetary table → status ok/mixed_low_text, table_degraded=True.
    ingest_pdf_pages must create a type='pdf' row with pdf_table_degraded=True in metadata."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    tuition_bytes = TUITION_PDF.read_bytes()

    def fetch(url):
        return tuition_bytes

    url = "https://bursar.njit.edu/tuition.pdf"
    result = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                              [(url, "Tuition Schedule")],
                              fetch)
    c.commit()

    assert result["pdf_inserted"] == 1, f"Expected 1 inserted, got: {result}"

    row = c.execute(
        "SELECT type, source_url, json_extract(metadata,'$.pdf_table_degraded'), "
        "json_extract(metadata,'$.natural_key'), created_by "
        "FROM knowledge_items WHERE source_url=? AND is_active=1",
        (url,)
    ).fetchone()
    assert row is not None, "No row inserted for tuition.pdf"
    rtype, rsrc, rdeg, rnk, rcb = row
    assert rtype == "pdf", f"type should be 'pdf', got {rtype!r}"
    assert rsrc == url
    assert rdeg == 1, f"pdf_table_degraded should be True (1), got {rdeg!r}"
    assert rnk == url, f"natural_key should be url, got {rnk!r}"
    assert rcb == "college_crawl"


def test_calendar_pdf_creates_clean_row():
    """calendar.pdf has normal text → table_degraded=False."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    cal_bytes = CALENDAR_PDF.read_bytes()

    def fetch(url):
        return cal_bytes

    url = "https://registrar.njit.edu/calendar.pdf"
    result = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                              [(url, "Academic Calendar")],
                              fetch)
    c.commit()

    assert result["pdf_inserted"] == 1, f"Expected 1 inserted, got: {result}"
    row = c.execute(
        "SELECT json_extract(metadata,'$.pdf_table_degraded') "
        "FROM knowledge_items WHERE source_url=? AND is_active=1",
        (url,)
    ).fetchone()
    assert row is not None
    # calendar is clean text, not a dense monetary table → degraded=False (0 or None)
    assert not row[0], f"pdf_table_degraded should be False/0, got {row[0]!r}"


def test_image_heavy_pdf_no_row_manifest_skip():
    """image_heavy_synth.pdf → no extractable text → NO row, manifest skip entry.
    The synthetic fixture's PDF-embedded images yield 0 text (status=empty) — both
    'image_heavy' and 'empty' are non-insertable; we assert the skip is recorded with
    a status that is NOT one of the insertable statuses."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    ih_bytes = IMAGE_HEAVY_PDF.read_bytes()

    def fetch(url):
        return ih_bytes

    url = "https://example.njit.edu/image_heavy.pdf"
    result = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                              [(url, "Image Heavy Doc")],
                              fetch)
    c.commit()

    assert result["pdf_inserted"] == 0
    assert result["pdf_updated"] == 0
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE source_url=?", (url,)).fetchone()[0]
    assert n == 0, f"No row should be inserted for non-text PDF, found {n}"
    # must have a manifest skip entry
    assert len(result["skipped"]) == 1
    skip = result["skipped"][0]
    assert skip["url"] == url
    # status must NOT be one of the insertable ones
    assert skip["status"] not in ("ok", "mixed_low_text"), (
        f"Non-insertable PDF should not have ok/mixed_low_text status, got {skip['status']!r}"
    )


def test_none_fetch_no_row_manifest_skip():
    """When fetch_bytes returns None, no row is created and a manifest skip is recorded."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()

    def fetch(url):
        return None

    url = "https://example.njit.edu/missing.pdf"
    result = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                              [(url, "Missing PDF")],
                              fetch)
    c.commit()

    assert result["pdf_inserted"] == 0
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE source_url=?", (url,)).fetchone()[0]
    assert n == 0
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["url"] == url
    assert result["skipped"][0]["status"] == "fetch_failed"


def test_idempotent_unchanged():
    """Running ingest_pdf_pages twice on the same unchanged bytes → second run reports unchanged,
    no duplicate row."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    cal_bytes = CALENDAR_PDF.read_bytes()

    def fetch(url):
        return cal_bytes

    url = "https://registrar.njit.edu/calendar2.pdf"
    result1 = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                               [(url, "Calendar v2")],
                               fetch)
    c.commit()
    assert result1["pdf_inserted"] == 1

    result2 = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                               [(url, "Calendar v2")],
                               fetch)
    c.commit()
    assert result2["pdf_unchanged"] == 1, f"Expected unchanged on 2nd run: {result2}"
    assert result2["pdf_inserted"] == 0

    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE source_url=? AND is_active=1",
                  (url,)).fetchone()[0]
    assert n == 1, f"Must be exactly 1 active row, found {n}"


def test_title_from_label_and_stem():
    """Title = label when non-empty; else a mechanical stem from the url filename."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    cal_bytes = CALENDAR_PDF.read_bytes()

    calls = []
    def fetch(url):
        calls.append(url)
        return cal_bytes

    # With a label
    url_with_label = "https://x.njit.edu/docs/academic-calendar-2025.pdf"
    ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                     [(url_with_label, "Spring 2025 Calendar")], fetch)
    row_label = c.execute("SELECT title FROM knowledge_items WHERE source_url=?",
                          (url_with_label,)).fetchone()
    assert row_label and row_label[0] == "Spring 2025 Calendar"

    # Without a label (empty string) → stem from URL
    url_no_label = "https://x.njit.edu/docs/graduate-catalog-2025.pdf"
    ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                     [(url_no_label, "")], fetch)
    row_stem = c.execute("SELECT title FROM knowledge_items WHERE source_url=?",
                         (url_no_label,)).fetchone()
    assert row_stem is not None
    # hyphens/underscores → spaces, filename without extension
    assert "graduate catalog 2025" in row_stem[0].lower()


def test_dedup_by_url():
    """Duplicate URLs in pdf_items are deduplicated inside ingest_pdf_pages."""
    from v2.core.ingestion.college_crawl import ingest_pdf_pages
    c = _conn()
    cal_bytes = CALENDAR_PDF.read_bytes()
    fetched = []

    def fetch(url):
        fetched.append(url)
        return cal_bytes

    url = "https://x.njit.edu/dup.pdf"
    result = ingest_pdf_pages(c, "ywcc", "YWCC", "njit",
                              [(url, "Dup"), (url, "Dup again")],
                              fetch)
    c.commit()
    # fetch called once (dedup), only 1 row
    assert fetched.count(url) == 1
    assert result["pdf_inserted"] == 1
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE source_url=?", (url,)).fetchone()[0]
    assert n == 1


# ── _build_context_block safeguard tests ─────────────────────────────────────

def _make_chunk(pdf_table_degraded=False, source_url=None):
    """Build a V1Chunk that looks like it came from the shim."""
    from v2.integration.retriever_shim import V1Chunk
    return V1Chunk(
        text="Tuition for full-time graduate is 660.00 per credit.",
        source_file="YWCC",
        source_type="pdf",
        section_title="Tuition Schedule",
        similarity=0.9,
        relevance_score=0.9,
        metadata={"pdf_table_degraded": pdf_table_degraded},
        source_url=source_url or "https://bursar.njit.edu/tuition.pdf",
    )


def test_context_block_has_safeguard_for_degraded():
    """When a chunk has pdf_table_degraded=True in metadata, the context block must contain
    the deterministic 'PDF table' safeguard line."""
    from bot.services.ollama_client import OllamaClient
    client = OllamaClient.__new__(OllamaClient)  # skip __init__
    chunk = _make_chunk(pdf_table_degraded=True)
    block = client._build_context_block([chunk])
    assert "pdf table" in block.lower() or "source link" in block.lower(), (
        f"Safeguard line not found in context block:\n{block}"
    )


def test_context_block_no_safeguard_for_clean():
    """When pdf_table_degraded is False (or absent), no safeguard line is added."""
    from bot.services.ollama_client import OllamaClient
    client = OllamaClient.__new__(OllamaClient)
    chunk = _make_chunk(pdf_table_degraded=False)
    block = client._build_context_block([chunk])
    # The safeguard phrase should not appear for clean PDFs
    assert "row/column figures" not in block, (
        f"Safeguard should be absent for clean PDF, found in:\n{block}"
    )
