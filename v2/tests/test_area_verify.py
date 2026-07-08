import v2.core.retrieval.area_expand as ax


def test_verify_selects_and_is_defensive():
    cands = ["cyber security", "network security", "machine learning"]
    # Prompt renders a 1-based list ("1. cyber security", "2. network security", ...), and the
    # few-shot examples answer with 1-based indices, so a real model returns 1-based indices too.
    stub = lambda system, prompt, schema: {"indices": [1, 2]}
    assert ax.llm_verify("cyber security", cands, verify=stub) == ["cyber security", "network security"]
    import pytest
    with pytest.raises(ax.VerifyError):                 # LLM error -> raise (orchestrator won't cache)
        ax.llm_verify("x", cands, verify=lambda *a: None)
    assert ax.llm_verify("x", cands, verify=lambda *a: {"indices": [99, -1, 0]}) == []  # out-of-range ignored (0 is out of range in 1-based)
