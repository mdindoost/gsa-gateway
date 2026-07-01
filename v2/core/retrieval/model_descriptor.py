"""Embedding-model descriptor — the single source of truth for model-specific limits.

This is the LLM-agnostic seam (feedback: the system must work with ANY embedding model)
and the max-capacity guard (feedback: use the model's strongest *measured* regime, not the
raw context ceiling). Chunk budget, overlap, truncation, vector dimension, and the embed
prefixes ALL read from the active descriptor — never a magic constant. Swapping models =
register a new descriptor + re-embed, with no code change.

`context_window` is the HARD truncation ceiling (tokens the model can accept at all).
`working_size` is the chunk TARGET (the model's strongest measured regime — for
nomic-embed-text that is 512, its MTEB eval length, well inside its 2048 native window).
They are deliberately distinct so a builder can never conflate "use full capacity" with
"embed at the raw ceiling" (which lowers quality).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tokenizers import Tokenizer

_ASSETS = Path(__file__).resolve().parent / "assets"


@lru_cache(maxsize=4)
def _load_tokenizer(path: str) -> Tokenizer:
    return Tokenizer.from_file(path)


@dataclass(frozen=True)
class ModelDescriptor:
    id: str               # stable id baked into chunk content_hash (e.g. "nomic-embed-text@v1.5")
    ollama_name: str      # the name Ollama serves it under
    tokenizer_file: str   # vendored tokenizer.json under assets/
    dim: int              # embedding dimension (vec0 table column width)
    context_window: int   # HARD ceiling in tokens (never embed more than this)
    working_size: int     # chunk target in tokens (strongest measured regime)
    overlap: int          # chunk overlap in tokens
    doc_prefix: str       # document embedding prefix
    query_prefix: str     # query embedding prefix

    @property
    def tokenizer(self) -> Tokenizer:
        return _load_tokenizer(str(_ASSETS / self.tokenizer_file))

    def count_tokens(self, text: str) -> int:
        # Count CONTENT tokens (exclude the model's [CLS]/[SEP], a constant +2 overhead
        # well inside the window) so count and truncate stay mutually consistent.
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Return the longest VERBATIM prefix of `text` that is <= max_tokens tokens.

        Slices the original string at the char boundary of the Nth token (lossless —
        no detokenization artifacts), replacing the old hardcoded ``text[:2000]`` slice.
        """
        enc = self.tokenizer.encode(text, add_special_tokens=False)
        if len(enc.ids) <= max_tokens:
            return text
        end_char = enc.offsets[max_tokens - 1][1]
        return text[:end_char]


NOMIC = ModelDescriptor(
    id="nomic-embed-text@v1.5",
    ollama_name="nomic-embed-text",
    tokenizer_file="nomic_tokenizer.json",
    dim=768,
    context_window=2048,
    working_size=512,
    overlap=77,           # ~15% of working_size
    doc_prefix="search_document: ",
    query_prefix="search_query: ",
)

# Qwen3-Embedding-0.6B — the production embedding model (switch 2026-06-30). 1024-d Matryoshka
# vectors stored at FULL width; ASYMMETRIC instruction prefix — the QUERY is wrapped in Qwen's
# `Instruct: {task}\nQuery: {text}` template (omitting it costs ~1-5% recall), PASSAGES/docs are
# embedded RAW. 32K native window. working_size stays 512 to isolate the model swap from any
# chunk-size change (the chunk regime is unchanged; only the model + dim + prefixes move).
QWEN = ModelDescriptor(
    id="qwen3-embedding-0.6b@v1",
    ollama_name="qwen3-embedding:0.6b",
    tokenizer_file="qwen3_tokenizer.json",
    dim=1024,
    context_window=32768,
    working_size=512,
    overlap=77,           # ~15% of working_size
    doc_prefix="",        # passages embedded raw (asymmetric)
    query_prefix=(
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery: "
    ),
)

_REGISTRY = {NOMIC.id: NOMIC, QWEN.id: QWEN}
_BY_OLLAMA = {NOMIC.ollama_name: NOMIC, QWEN.ollama_name: QWEN}

# The production default. `EMBEDDING_MODEL` (an Ollama name) overrides it so the fallback model
# (nomic) stays selectable without a code change; an unknown value falls back to the default.
DEFAULT_DESCRIPTOR = QWEN


def active_descriptor() -> ModelDescriptor:
    """The descriptor the pipeline currently uses; env-selectable by Ollama model name."""
    name = os.environ.get("EMBEDDING_MODEL")
    if name and name in _BY_OLLAMA:
        return _BY_OLLAMA[name]
    return DEFAULT_DESCRIPTOR


def get_descriptor(descriptor_id: str) -> ModelDescriptor:
    return _REGISTRY[descriptor_id]
