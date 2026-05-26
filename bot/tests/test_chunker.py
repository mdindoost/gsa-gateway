"""Tests for the DocumentChunker service."""

from pathlib import Path

import pytest

from bot.services.chunker import MAX_TOKENS, DocumentChunk, DocumentChunker


@pytest.fixture
def real_data_dir() -> Path:
    return Path(__file__).parent.parent / "data"


@pytest.fixture
def chunker(real_data_dir) -> DocumentChunker:
    return DocumentChunker(data_dir=real_data_dir)


def test_faq_chunking_produces_correct_count(chunker, real_data_dir):
    chunks = chunker.chunk_markdown_faq(real_data_dir / "gsa_faq.md")
    assert len(chunks) > 0, "Expected at least some FAQ chunks"
    # Each Q&A pair should produce at least one chunk
    for chunk in chunks:
        assert chunk.source_type == "faq"
        assert chunk.text.startswith("Question:")
        assert "Answer" in chunk.text


def test_policy_chunking_respects_token_limit(chunker, real_data_dir):
    for filename in ("gsa_constitution.md", "travel_award.md", "club_finance.md", "rules.md"):
        filepath = real_data_dir / filename
        if not filepath.exists():
            continue
        chunks = chunker.chunk_markdown_policy(filepath)
        for chunk in chunks:
            assert chunk.token_count <= MAX_TOKENS, (
                f"Chunk in {filename} exceeds token limit: "
                f"{chunk.token_count} > {MAX_TOKENS}"
            )


def test_overlap_between_chunks(chunker):
    long_text = ". ".join([f"This is sentence number {i} with some content here" for i in range(60)])
    parts = chunker.split_text_by_tokens(long_text)
    assert len(parts) >= 2, "Expected text to be split into multiple chunks"
    # Consecutive chunks should share some content (overlap)
    for i in range(len(parts) - 1):
        words_a = set(parts[i].lower().split())
        words_b = set(parts[i + 1].lower().split())
        shared = words_a & words_b
        assert len(shared) > 0, f"No overlap between chunk {i} and {i+1}"


def test_yaml_event_chunking(chunker, real_data_dir):
    chunks = chunker.chunk_yaml_events(real_data_dir / "events.yml")
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.source_type == "event"
        assert "Event:" in chunk.text
        assert "Date:" in chunk.text
        assert "Location:" in chunk.text


def test_yaml_contact_chunking(chunker, real_data_dir):
    chunks = chunker.chunk_yaml_contacts(real_data_dir / "contacts.yml")
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.source_type == "contact"
        # Each contact chunk should contain name and email info
        has_name = "Name:" in chunk.text or "GSA Officer" in chunk.text or "Campus Office" in chunk.text
        assert has_name, f"Contact chunk missing name info: {chunk.text[:100]}"


def test_chunk_id_uniqueness(chunker):
    chunks = chunker.chunk_all()
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "Duplicate chunk IDs found"


def test_missing_file_handled_gracefully(tmp_path):
    empty_dir = tmp_path / "empty_data"
    empty_dir.mkdir()
    chunker = DocumentChunker(data_dir=empty_dir)
    # Should not raise, just log warnings and return empty list
    chunks = chunker.chunk_all()
    assert isinstance(chunks, list)
    assert len(chunks) == 0


def test_all_chunks_have_required_fields(chunker):
    chunks = chunker.chunk_all()
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.chunk_id
        assert chunk.text
        assert chunk.source_file
        assert chunk.source_type in ("faq", "policy", "event", "contact", "resource")
        assert chunk.token_count > 0
