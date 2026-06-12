"""The LLM context block labels documents by their knowledge_items id (doc_id N)."""
from types import SimpleNamespace

from bot.services.ollama_client import OllamaClient


def _chunk(**kw):
    base = dict(source_file="FAQ", section_title="S", text="body",
                relevance_score=0.6, item_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_doc_id_label_used_when_id_present():
    block = OllamaClient._build_context_block(None, [
        _chunk(source_file="Computer Science", section_title="Baruch Schieber",
               text="algorithms", relevance_score=0.71, item_id=170)])
    assert "[doc_id 170: Computer Science]" in block


def test_falls_back_to_positional_without_id():
    block = OllamaClient._build_context_block(None, [_chunk(item_id=None)])
    assert "[Document 1: FAQ]" in block


def test_source_url_rendered_when_present():
    block = OllamaClient._build_context_block(None, [
        _chunk(item_id=170, source_url="https://people.njit.edu/profile/ikoutis")])
    assert "Source: https://people.njit.edu/profile/ikoutis" in block


def test_verified_item_has_no_warning_tag():
    block = OllamaClient._build_context_block(None, [_chunk(item_id=1, verified=True)])
    assert "UNVERIFIED DRAFT" not in block


def test_unverified_item_is_flagged():
    block = OllamaClient._build_context_block(None, [_chunk(item_id=1, verified=False)])
    assert "UNVERIFIED DRAFT" in block


def test_full_item_text_is_not_truncated():
    # decomposition makes items small; the prompt no longer truncates them
    long_text = "graph " * 600  # 3600 chars, well past the old 1500 cap
    block = OllamaClient._build_context_block(None, [_chunk(item_id=1, text=long_text)])
    assert long_text in block
