import sys, urllib.error
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt import oracle_brave
from eval.processing_debt.oracle_brave import ask_oracle

_CONTENT = ('Pan Xu is an Assistant Professor at NJIT.'
            '<citation>{"url": "https://cs.njit.edu/pan-xu", "snippet": "Pan Xu, Assistant Professor",'
            ' "number": 1}</citation>')

def _fake_http_factory(counter, seen_body=None):
    def _http(url, body, headers):
        counter.append(1)
        if seen_body is not None:
            seen_body.append(body)
        return _CONTENT
    return _http

def test_ask_oracle_parses_citations_strips_tags_and_caches(tmp_path):
    oracle_brave.reset_spend()
    calls, bodies = [], []
    oa = ask_oracle("who is pan xu", cache_dir=str(tmp_path),
                    http=_fake_http_factory(calls, bodies), keys=["k"])
    assert oa.answer == "Pan Xu is an Assistant Professor at NJIT."      # <citation> stripped
    assert oa.citations[0].url == "https://cs.njit.edu/pan-xu"
    assert oa.citations[0].snippet == "Pan Xu, Assistant Professor"
    import json as _json
    sent = _json.loads(bodies[0])
    assert sent["stream"] is True and sent["enable_citations"] is True   # the fix
    assert len(calls) == 1
    oa2 = ask_oracle("who is pan xu", cache_dir=str(tmp_path), http=_fake_http_factory(calls), keys=["k"])
    assert oa2.answer == oa.answer and len(calls) == 1                    # cache hit

def test_no_citation_tags_yields_empty_citations(tmp_path):
    oracle_brave.reset_spend()
    oa = ask_oracle("plain q", cache_dir=str(tmp_path),
                    http=lambda u, b, h: "Just prose, no tags.", keys=["k"])
    assert oa.answer == "Just prose, no tags." and oa.citations == []

def test_rotates_to_second_key_on_429(tmp_path):
    oracle_brave.reset_spend()
    def _http(url, body, headers):
        if headers["X-Subscription-Token"] == "k1":
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return "ok from k2"
    oa = ask_oracle("q rotate", cache_dir=str(tmp_path), http=_http, keys=["k1", "k2"])
    assert oa.answer == "ok from k2"

def test_spend_guard_blocks_over_cap(tmp_path):
    oracle_brave.reset_spend()
    http = _fake_http_factory([])
    ask_oracle("q one", cache_dir=str(tmp_path), http=http, keys=["k"], max_live=1)
    import pytest
    with pytest.raises(RuntimeError):
        ask_oracle("q two different", cache_dir=str(tmp_path), http=http, keys=["k"], max_live=1)
