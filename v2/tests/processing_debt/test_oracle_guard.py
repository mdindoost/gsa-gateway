import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import OracleAnswer, OracleCitation, Nugget
from eval.processing_debt.oracle_guard import guard

def _oracle():
    return OracleAnswer("q", "ans", [OracleCitation("https://cs.njit.edu/x")])

def test_guard_supported_when_page_entails():
    v = guard(Nugget("Pan Xu is Assistant Professor", True), _oracle(),
              fetch=lambda u: "Pan Xu, Assistant Professor of CS", entails_fn=lambda f, t: True,
              is_internal=lambda f: False)
    assert v.verdict == "supported"

def test_guard_unsupported_when_page_silent():
    v = guard(Nugget("Pan Xu won a Nobel Prize", True), _oracle(),
              fetch=lambda u: "Pan Xu, Assistant Professor of CS", entails_fn=lambda f, t: False,
              is_internal=lambda f: False)
    assert v.verdict == "unsupported"

def test_guard_authority_flag_on_internal():
    v = guard(Nugget("The GSA president is Alice", True), _oracle(),
              fetch=lambda u: "irrelevant", entails_fn=lambda f, t: False,
              is_internal=lambda f: True)
    assert v.verdict == "we_are_authority"

def test_guard_supported_via_snippet_without_fetch():
    from eval.processing_debt.types import OracleAnswer, OracleCitation, Nugget, GuardVerdict
    from eval.processing_debt.oracle_guard import guard
    oa = OracleAnswer("q", "answer",
                      citations=[OracleCitation(url="http://x", snippet="Vincent Oria is the CS chair at NJIT")])
    def _no_fetch(url):
        raise AssertionError("must not fetch when the snippet already supports the fact")
    v = guard(Nugget("Vincent Oria is the chair of Computer Science", True), oa,
              fetch=_no_fetch, entails_fn=lambda fact, text: "Oria" in text and "chair" in text,
              is_internal=lambda f: False)
    assert v.verdict == "supported" and "Oria" in (v.evidence_span or "")


def test_guard_windows_full_page_not_truncated():
    # regression: "Vincent Oria ... Chair" lived at char ~11000 of the real page; the old page[:8000]
    # cap cut it off BEFORE windowing -> the fact was wrongly DROPPED. The guard must see the whole page.
    from eval.processing_debt.types import OracleAnswer, OracleCitation, Nugget
    oa = OracleAnswer("q", "ans", citations=[OracleCitation(url="http://x")])   # no snippet -> forces fetch
    long_page = ("boilerplate " * 900) + "MARKER_PAST_8000"                     # marker at ~10800 chars
    seen = {}
    def _entails(fact, text):
        seen["len"] = len(text)
        return "MARKER_PAST_8000" in text
    v = guard(Nugget("some fact", True), oa, fetch=lambda u: long_page, entails_fn=_entails)
    assert v.verdict == "supported"                 # rescued because the full page was passed
    assert seen["len"] > 8000                       # not truncated to 8000
