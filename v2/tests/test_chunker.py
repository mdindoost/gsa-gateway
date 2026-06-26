from v2.core.retrieval.chunker import chunk_text
from v2.core.retrieval.model_descriptor import active_descriptor

D = active_descriptor()


def test_empty_text_yields_no_chunks():
    assert chunk_text("   ", D) == []


def test_short_item_is_one_chunk_identical():
    txt = "The Registrar handles registration holds for graduate students."
    assert chunk_text(txt, D) == [txt]


def test_long_text_splits_and_each_within_budget():
    txt = ("The Office of the Registrar processes registration holds and transcript "
           "requests. Students must resolve advising holds before registration. ") * 120
    chunks = chunk_text(txt, D)
    assert len(chunks) > 1
    for c in chunks:
        assert D.count_tokens(c) <= D.working_size
        assert c in txt                      # verbatim substring of the parent


def test_chunks_overlap_and_cover_all_content():
    txt = ("Sentence number one is here. Sentence two follows it. Third sentence appears now. ") * 100
    chunks = chunk_text(txt, D)
    # consecutive chunks share text (overlap > 0)
    assert any(chunks[i].split()[-3:] == chunks[i + 1].split()[:0] or
               chunks[i][-30:] in chunks[i + 1] or chunks[i + 1][:30] in chunks[i]
               for i in range(len(chunks) - 1)) or len(chunks) == 1
    # coverage: first chunk starts at the start, last ends at the end
    assert txt.strip().startswith(chunks[0][:20])
    assert txt.strip().endswith(chunks[-1][-20:])


def test_monster_sentence_no_punctuation_still_bounded():
    txt = "word " * 4000          # one giant run, no sentence enders
    chunks = chunk_text(txt, D)
    assert len(chunks) > 1
    for c in chunks:
        assert D.count_tokens(c) <= D.working_size
