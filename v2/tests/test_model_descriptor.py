from v2.core.retrieval.model_descriptor import NOMIC, active_descriptor, get_descriptor


def test_nomic_descriptor_shape():
    d = active_descriptor()
    assert d is NOMIC
    assert d.dim == 768
    assert d.working_size == 512
    assert d.context_window == 2048
    assert d.working_size < d.context_window      # strongest regime < raw ceiling
    assert get_descriptor("nomic-embed-text@v1.5") is NOMIC


def test_count_tokens_uses_real_tokenizer():
    d = active_descriptor()
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
