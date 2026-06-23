from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.retrieval.structured_answer import (
    format_answer, deterministic_suffix, is_deterministic,
)


def _mop(metric_key, found, allm, updated="2026-06", name="Ioannis Koutis"):
    return {"skill": "metric_of_person", "name": name, "field_key": "scholar",
            "metric_key": metric_key, "found": found, "all": allm, "updated_at": updated}


def _top(ranked, with_metric, total, n=1, org="Computer Science"):
    return {"skill": "top_people_by_metric", "org_name": org, "field_key": "scholar",
            "metric_key": "citations", "n": n, "ranked": ranked,
            "with_metric": with_metric, "total_in_org": total}


# ── metric_of_person ──────────────────────────────────────────────────────────
def test_metric_of_person_has_metric():
    out = format_answer(_mop("citations", {"citations": 2774},
                             {"citations": 2774, "h_index": 26, "i10_index": 35}))
    assert "Ioannis Koutis" in out
    assert "2,774 citations" in out
    assert "2026-06" in out


def test_metric_of_person_partial_offers_what_we_have():
    out = format_answer(_mop("h_index", {}, {"citations": 100}, updated=None))
    assert "don't have" in out.lower() or "do not have" in out.lower()
    assert "h-index" in out
    assert "100 citations" in out  # offers the metric we DO have


def test_metric_of_person_honest_empty():
    out = format_answer(_mop("citations", {}, {}, updated=None, name="Pat X"))
    assert "don't have scholar metrics on file for pat x" in out.lower()


# ── top_people_by_metric ──────────────────────────────────────────────────────
def test_ranking_partial_states_both_numbers_and_caveat():
    out = format_answer(_top([("Ioannis Koutis", 2774), ("Low Cite", 100)],
                             with_metric=2, total=5, n=1))
    assert "2" in out and "5" in out                # with_metric and total_in_org
    assert "full ranking" in out.lower()
    assert "Ioannis Koutis" in out
    assert "2,774 citations" in out


def test_ranking_n1_tie_names_all_tied():
    out = format_answer(_top([("Aaron Tie", 2774), ("Xavier Tie", 2774)],
                             with_metric=2, total=2, n=1))
    assert "Aaron Tie" in out and "Xavier Tie" in out


def test_ranking_full_coverage_drops_caveat():
    out = format_answer(_top([("A One", 50), ("B Two", 40)], with_metric=2, total=2, n=1))
    assert "full ranking" not in out.lower()


def test_ranking_topn_more_than_available_shows_actual():
    out = format_answer(_top([("A One", 50), ("B Two", 40)], with_metric=2, total=10, n=5))
    assert "A One" in out and "B Two" in out
    assert "2" in out and "10" in out  # actual 2 of 10, not "top 5"


def test_ranking_empty_is_honest():
    out = format_answer(_top([], with_metric=0, total=4, n=1))
    assert "don't have scholar metrics on file for anyone in computer science" in out.lower()


# ── deterministic guarantees ──────────────────────────────────────────────────
def test_metric_skills_are_deterministic_no_compose():
    assert is_deterministic(_mop("citations", {"citations": 1}, {"citations": 1})) is True
    assert is_deterministic(_top([("A", 1)], 1, 1)) is True
    assert is_deterministic({"skill": "entity_card", "card": "x"}) is False


def test_deterministic_suffix_does_not_double_fire_on_metric_skills():
    assert deterministic_suffix(_mop("citations", {"citations": 1}, {"citations": 1})) is None
    assert deterministic_suffix(_top([("A", 1)], 1, 1)) is None


# ── metric_descending_unsupported (Bug B, Option 3) ────────────────────────────
def _decline(metric_key="citations"):
    return {"skill": "metric_descending_unsupported",
            "field_key": "scholar", "metric_key": metric_key}


def test_descending_decline_names_no_person_and_offers_most():
    out = format_answer(_decline("citations"))
    assert out != ""                                     # TERMINAL — must not fall to RAG
    assert "citations" in out.lower()
    assert "most" in out.lower()                         # offers the highest alternative
    # no specific person named, and no baked coverage numbers
    assert "koutis" not in out.lower()
    assert "211" not in out and "1,076" not in out and "1076" not in out


def test_descending_decline_uses_metric_noun():
    out = format_answer(_decline("h_index"))
    assert "h-index" in out.lower()


def test_descending_decline_is_deterministic():
    # must be in _DETERMINISTIC_SKILLS so the LLM is never invoked to reword it
    assert is_deterministic(_decline()) is True


# ── link_of_person rendering (Facet B) ─────────────────────────────────────────
def _link(url, label="LinkedIn", name="Vincent Oria", field_key="linkedin"):
    return {"skill": "link_of_person", "name": name, "field_label": label,
            "field_key": field_key, "url": url}


def test_link_has_url():
    out = format_answer(_link("https://www.linkedin.com/in/vincent-oria-7b06a114"))
    assert "Vincent Oria's LinkedIn" in out
    assert "https://www.linkedin.com/in/vincent-oria-7b06a114" in out


def test_link_honest_empty_is_terminal_not_blank():
    out = format_answer(_link(None, label="GitHub"))
    assert out != ""                                  # TERMINAL — must NOT fall through to RAG
    assert "don't have a github on file" in out.lower()


def test_link_is_deterministic():
    assert is_deterministic(_link("x")) is True
