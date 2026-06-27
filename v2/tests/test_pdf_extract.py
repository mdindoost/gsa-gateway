# v2/tests/test_pdf_extract.py
from pathlib import Path
from v2.core.ingestion.pdf_extract import extract_pdf_text

FIX = Path(__file__).parent / "fixtures" / "pdf"


def test_prose_pdf_extracts_clean_text():
    r = extract_pdf_text(FIX / "calendar.pdf")
    assert r.status == "ok"
    assert r.text and "Academic Calendar" in r.text
    # newline->space normalization: no raw newlines, no mid-word joins of separate words
    assert "\n" not in r.text
    assert "Last Day to Add/Drop a Class" in r.text       # facts intact, words separated


def test_cleanup_is_text_preserving_no_token_join():
    # wrapped separate words must keep their boundary (finding #8): "an undergraduate" not "anundergraduate"
    raw = "as part of an\nundergraduate program"
    from v2.core.ingestion.pdf_extract import _clean
    assert _clean(raw) == "as part of an undergraduate program"


def test_dense_numeric_table_flagged_degraded():
    r = extract_pdf_text(FIX / "tuition.pdf")
    assert r.status == "ok"
    assert r.table_degraded is True            # tuition schedule = degraded numeric grid
    assert "Tuition and Fees" in r.text


def test_invalid_pdf_skipped():
    r = extract_pdf_text(b"<!DOCTYPE html><html>not a pdf</html>")
    assert r.status == "invalid"
    assert r.text is None


def test_image_heavy_pdf_skipped():
    # Pre-generated committed fixture: 8 blank pages + 300KB padding bytes
    # -> low chars/page AND high bytes/text-char -> triggers image_heavy or empty heuristic.
    # Generated 2026-06-27; sha256: 3d4fb0e95c49719fc37c6a9bf329bb4e0530a1c3b05986d0cf20594b4cfe800a
    r = extract_pdf_text(FIX / "image_heavy_synth.pdf")
    assert r.status in ("image_heavy", "empty")
    assert r.text is None
