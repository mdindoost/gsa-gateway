from v2.core.retrieval.model_descriptor import (
    NOMIC, QWEN, active_descriptor, get_descriptor,
)


def test_nomic_descriptor_shape():
    # NOMIC stays registered + selectable by env (fallback model, still pulled).
    d = get_descriptor("nomic-embed-text@v1.5")
    assert d is NOMIC
    assert d.dim == 768
    assert d.working_size == 512
    assert d.context_window == 2048
    assert d.working_size < d.context_window      # strongest regime < raw ceiling


def test_qwen_descriptor_shape():
    d = QWEN
    assert d.dim == 1024                           # full Matryoshka width, stored un-truncated
    assert d.ollama_name == "qwen3-embedding:0.6b"
    assert d.context_window == 32768               # 32K native window
    assert d.working_size < d.context_window
    assert get_descriptor(QWEN.id) is QWEN


def test_qwen_asymmetric_prefix():
    # Query gets the Instruct wrapper; passages/docs are embedded RAW (no prefix).
    d = QWEN
    assert d.doc_prefix == ""
    assert d.query_prefix == (
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery: "
    )


def test_active_descriptor_defaults_to_qwen(monkeypatch):
    # The 2026-06-30 production switch: QWEN is the default active model.
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    assert active_descriptor() is QWEN


def test_active_descriptor_env_selects_nomic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    assert active_descriptor() is NOMIC


def test_active_descriptor_env_selects_qwen(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    assert active_descriptor() is QWEN


def test_qwen_tokenizer_loads_and_counts():
    d = QWEN
    assert d.count_tokens("registration hold transcript request") > 3


def test_count_tokens_uses_real_tokenizer():
    d = get_descriptor("nomic-embed-text@v1.5")
    assert d.count_tokens("registration hold transcript request") > 3


def test_truncate_short_text_unchanged():
    d = active_descriptor()
    txt = "a short policy sentence."
    assert d.truncate_to_tokens(txt, 512) == txt


def test_truncate_is_verbatim_prefix_within_budget():
    d = active_descriptor()
    txt = "The Office of the Registrar processes registration holds. " * 200
    out = d.truncate_to_tokens(txt, 50)
    assert txt.startswith(out)            # verbatim prefix, no detok artifacts
    assert out != txt                      # actually truncated
    assert d.count_tokens(out) <= 50       # within budget
