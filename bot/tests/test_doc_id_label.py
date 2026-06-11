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
