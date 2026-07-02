"""WS2 — fuzzy entity resolution + the never-guess gate (person side, Phase 1+3).

The safety contract (WS2 review, owner-approved Option 1):
- Clean input resolves at the EXACT tier and never reaches fuzzy (no behavior change).
- A fuzzy name NEVER auto-resolves on string similarity alone — a 1-edit typo of an ABSENT person is
  score-indistinguishable from a typo of a present one. It auto-resolves ONLY when structurally
  corroborated (the query names an org the candidate belongs to); otherwise it CLARIFYs.
- Nonsense → ABSTAIN. A skill is NEVER executed on a fuzzy-but-unvalidated slot.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval import entity
from v2.core.retrieval.router import Route
from v2.core.retrieval.slot_extractor import resolve_and_validate, _resolve_person_slot

try:
    import symspellpy  # noqa: F401
    HAVE_SYMSPELL = True
except Exception:
    HAVE_SYMSPELL = False


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ee = ensure_org(c, "ee", "Electrical Engineering", "njit", type="department")
    ensure_org(c, "me", "Mechanical Engineering", "njit", type="department")   # 2nd 'engineering'
    sync_org_nodes(c)

    def appt(key, name, org, title, cat="faculty"):
        project_appointment(c, person_key=key, name=name, org_id=org, category=cat,
                            titles=[title], source_section="manual", source="dashboard")
    appt("d/koutis", "Ioannis Koutis", cs, "Professor")           # typo target (unique surname, in CS)
    appt("d/wang1", "Guiling Wang", cs, "Professor")              # ambiguous surname
    appt("d/wang2", "Jian Wang", cs, "Professor")
    appt("d/singh", "Pushpendra Singh", ee, "Professor")          # near-miss target ('sing')
    c.commit()
    yield c
    c.close()


# ── entity.fuzzy_people (candidate generation) ─────────────────────────────────────────────────────
def test_fuzzy_people_recovers_typo(conn):
    hits = entity.fuzzy_people(conn, "kotis")
    assert [h["name"] for h in hits] == ["Ioannis Koutis"]
    assert hits[0]["score"] >= entity._FUZZY_PERSON_CUTOFF


def test_fuzzy_people_nonsense_is_empty(conn):
    assert entity.fuzzy_people(conn, "zzyzxson") == []


def test_fuzzy_people_is_fallback_only_clean_input_still_exact(conn):
    # Clean surname resolves via the exact tier; fuzzy is never consulted for it.
    st = _resolve_person_slot(conn, "Koutis", "who is koutis")
    assert st[0] == "ok" and st[3] == "exact" and st[1] == "d/koutis"


# ── the never-guess gate ───────────────────────────────────────────────────────────────────────────
def test_typo_without_corroboration_clarifies_not_resolves(conn):
    # bare "who is kotis" — no org named → must CLARIFY ("did you mean Koutis?"), NOT card-answer.
    r = resolve_and_validate(conn, "entity_card", {"person": "kotis"}, "who is kotis")
    assert isinstance(r, Route) and r.skill == "person_disambig"
    assert [c["name"] for c in r.args["candidates"]] == ["Ioannis Koutis"]


def test_typo_metric_without_corroboration_clarifies(conn):
    r = resolve_and_validate(conn, "metric_of_person",
                             {"person": "kotis", "metric": "h_index"}, "kotis h-index")
    assert r.skill == "person_disambig"          # never silently runs metric on a fuzzy slot


def test_typo_with_org_corroboration_resolves(conn):
    # "kotis ... in computer science" — the org corroborates the one real Koutis → safe auto-resolve.
    r = resolve_and_validate(conn, "research_of_person",
                             {"person": "kotis"}, "kotis research in computer science")
    assert r.skill == "research_of_person" and r.args["entity_id"] == "d/koutis"


def test_near_miss_absent_person_never_auto_resolves(conn):
    # "sing" is 1 edit from the unique surname "Singh" — a lone candidate. It must CLARIFY, never
    # silently answer as Pushpendra Singh (the anti-fabrication invariant).
    r = resolve_and_validate(conn, "entity_card", {"person": "sing"}, "who is sing")
    assert r is None or r.skill == "person_disambig"
    if r is not None:
        assert all(c["name"] != "Pushpendra Singh" for c in r.args["candidates"]) or \
            r.skill == "person_disambig"          # if surfaced at all, only as a "did you mean", not a card


def test_near_miss_wrong_org_does_not_corroborate(conn):
    # Singh is in EE, not CS — naming CS must NOT corroborate 'sing' onto Singh.
    r = resolve_and_validate(conn, "research_of_person",
                             {"person": "sing"}, "sing research in computer science")
    assert r is None or r.skill == "person_disambig"


def test_nonsense_person_abstains(conn):
    assert resolve_and_validate(conn, "entity_card", {"person": "Zzyzxson"}, "who is zzyzxson") is None


def test_clean_ambiguous_surname_still_disambiguates(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Wang"}, "who is wang")
    assert r.skill == "person_disambig" and len(r.args["candidates"]) == 2


def test_clean_exact_name_resolves(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Koutis"}, "who is koutis")
    assert r.skill == "entity_card" and r.args["entity_id"] == "d/koutis"


# ── org head-word resolution (Phase 2) ─────────────────────────────────────────────────────────────
def _oid(conn, slug):
    return conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()[0]


def test_org_headword_resolves_single(conn):
    from v2.core.retrieval.router import fuzzy_org
    fz = fuzzy_org(conn, "electrical")
    assert [o for o, _ in fz] == [_oid(conn, "ee")]


def test_org_headword_gate_resolves(conn):
    r = resolve_and_validate(conn, "faculty_in_department", {"org": "electrical"},
                             "faculty in electrical")
    assert r.skill == "faculty_in_department" and r.args["org_id"] == _oid(conn, "ee")


def test_broad_org_word_is_ambiguous_and_abstains(conn):
    # "engineering" matches EE + ME → 2 distinct → org-required skill ABSTAINS (never picks one).
    from v2.core.retrieval.router import fuzzy_org
    assert len(fuzzy_org(conn, "engineering")) >= 2
    assert resolve_and_validate(conn, "faculty_in_department", {"org": "engineering"},
                                "faculty in engineering") is None


def test_broad_org_optional_skill_abstains_not_root(conn):
    # org-OPTIONAL skill: a named-but-ambiguous org must ABSTAIN, NOT silently default to root.
    r = resolve_and_validate(conn, "top_people_by_metric",
                             {"metric": "h_index", "org": "engineering"},
                             "top h-index in engineering")
    assert r is None


def test_generic_org_word_does_not_resolve(conn):
    from v2.core.retrieval.router import fuzzy_org
    assert fuzzy_org(conn, "department") == []
    assert fuzzy_org(conn, "atlantis") == []


@pytest.mark.skipif(not HAVE_SYMSPELL, reason="symspellpy not installed")
def test_symspell_recovers_two_edit_surname_typo(conn):
    # 'koutas' scores 83 on the raw scan (below cutoff) → recovered only via the symspell pass.
    # Uncorroborated → still a CLARIFY, but it must surface Koutis as the candidate.
    r = resolve_and_validate(conn, "entity_card", {"person": "koutas"}, "who is koutas")
    assert r is not None and r.skill == "person_disambig"
    assert [c["name"] for c in r.args["candidates"]] == ["Ioannis Koutis"]
