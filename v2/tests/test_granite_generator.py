"""The RAG generator defaults to IBM Granite 4.0 H-Tiny; llama3.1:8b stays selectable as a
fallback via OLLAMA_MODEL. Routing + embedding models are untouched by this swap."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.config import load_config
from bot.services.ollama_client import OllamaClient

GRANITE = "granite4:tiny-h"


def test_config_default_generator_is_granite(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert load_config().ollama_model == GRANITE


def test_config_env_overrides_to_llama_fallback(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    assert load_config().ollama_model == "llama3.1:8b"


def test_ollamaclient_default_model_is_granite():
    assert OllamaClient().model == GRANITE


def test_embedding_model_unchanged_by_generator_swap():
    # The generator swap must NOT move the embedding model off its own knob.
    c = OllamaClient()
    assert c.embedding_model != GRANITE
