"""F — abstain-hint for genuinely-ambiguous bare role/officer fragments.

Design: docs/superpowers/specs/2026-07-03-router-ambiguous-bare-role-abstain-design.md

F adds a terminal branch at the END of route() catching org-less bare role/officer fragments
that today fall to None -> live-fallback -> confident-wrong (verified: "officers" -> AFROTC).
Two-way dispatch:
  branch 1 (role vocab: dean/chair/director/coordinator…) -> people_by_role(role, org_id=None)
  branch 2 (officer titles: officers/treasurer/secretary/vp…) -> static ambiguous_officers deflect
Gate: branch regex matches + no process cue + zero non-stopword residue (strip DISPATCH regex only);
org test is branch-asymmetric — branch 1 strict `org is None`; branch 2 `org is None OR
(org is root AND not _has_true_officers)` (the njit-officers root-clause).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval.router import route, Route
from v2.core.retrieval import structured_answer as sa
from v2.core.retrieval.unified_router import UnifiedRouter, _FASTPATH_CUE

_HINT = ('I\'m not sure which organization you mean — try naming it, '
         'e.g. "GWICS officers" or "GSA officers".')


# ── fixture helpers (mirror test_router_org_role_collision) ──────────────────────
def _org(c, oid, name, slug, typ, parent=None, aliases=None):
    meta = json.dumps({"aliases": aliases}) if aliases else "{}"
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type,metadata) VALUES(?,?,?,?,?,?)",
              (oid, parent, name, slug, typ, meta))
    c.execute("INSERT INTO nodes(type,key,name,attrs,source,ontology_version,is_active,"
              "created_at,updated_at) VALUES('Org',?,?,?,'test',1,1,'','')",
              (f"org:{slug}", name, json.dumps({"org_id": oid})))
    return c.execute("SELECT id FROM nodes WHERE key=?", (f"org:{slug}",)).fetchone()[0]


def _person(c, name, key):
    c.execute("INSERT INTO nodes(type,key,name,attrs,source,ontology_version,is_active,"
              "created_at,updated_at) VALUES('Person',?,?, '{}', 'test',1,1,'','')", (key, name))
    return c.execute("SELECT id FROM nodes WHERE key=?", (key,)).fetchone()[0]


def _role(c, person_id, org_node_id, category, titles):
    c.execute("INSERT INTO edges(src_id,type,dst_id,category,attrs,source,ontology_version,"
              "is_active,created_at,updated_at) VALUES(?, 'has_role', ?, ?, ?, 'test',1,1,'','')",
              (person_id, org_node_id, category, json.dumps({"titles": titles})))


def _build(c):
    njit = _org(c, 1, "NJIT", "njit", "university")
    gsa = _org(c, 2, "Graduate Student Association", "gsa", "custom", parent=1)
    ywcc = _org(c, 4, "Ying Wu College of Computing", "ywcc", "college", parent=1)
    _org(c, 5, "Computer Science", "computer-science", "department", parent=4,
         aliases=["cs", "computer science"])
    _org(c, 52, "Office of the President", "president", "office", parent=1)
    _org(c, 53, "Office of the Provost", "provost", "office", parent=1)
    _org(c, 24, "Office of the University Registrar", "registrar", "department", parent=1,
         aliases=["registrar"])
    # root NJIT: admin 'President' title, but NO officer/deprep -> _has_true_officers(root) False
    _role(c, _person(c, "Teik C. Lim", "p:lim"), njit, "admin", ["President"])
    # GSA: true officers -> _has_true_officers(gsa) True (backs the E exclusion "gsa officers").
    # Surnames deliberately NOT role/officer words (else the surname branch would intercept the bare
    # fragment before F).
    _role(c, _person(c, "Alice Kim", "p:alice"), gsa, "officer", ["President"])
    _role(c, _person(c, "Grace Park", "p:grace"), gsa, "officer", ["Treasurer"])
    # YWCC: admin leadership only (a 'dean' + a 'director' + a 'coordinator' holder for branch-1);
    # NO officer/deprep. First names may be role-ish; SURNAMES are not.
    _role(c, _person(c, "Dean Payton", "p:payton"), ywcc, "admin",
          ["Dean, Ying Wu College of Computing"])
    _role(c, _person(c, "Rita Adkins", "p:rita"), ywcc, "admin", ["Director of Undergraduate Advising"])
    _role(c, _person(c, "Cory Bello", "p:cory"), ywcc, "admin", ["Coordinator"])
    c.commit()


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    _build(c)
    yield c
    c.close()


def _skill(r):
    return r.skill if r is not None else None


def _org_of(r):
    return r.args.get("org_id") if r is not None else None


# ── branch 2: officer titles, org-less -> ambiguous_officers ─────────────────────
@pytest.mark.parametrize("q", ["officers", "officer", "who are the officers", "treasurer",
                               "secretary", "vp", "e-board", "executive board"])
def test_branch2_orgless_officer_words_deflect(conn, q):
    assert _skill(route(conn, q)) == "ambiguous_officers"


# ── branch 2: root-org clause (RAG #1 — the njit-officers AFROTC sibling) ─────────
@pytest.mark.parametrize("q", ["njit officers", "officers at njit"])
def test_branch2_root_no_true_officers_deflects(conn, q):
    # resolves the university root, which has NO officer/deprep edge -> root clause fires.
    assert _skill(route(conn, q)) == "ambiguous_officers"


# ── branch 2 exclusions: a NON-root org resolves -> D's territory, F skips ────────
def test_gsa_officers_stays_E_not_ambiguous(conn):
    r = route(conn, "gsa officers")           # E fires (gsa holds true officers)
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_ywcc_officers_stays_none_D_territory(conn):
    # ywcc resolves org 4 (non-root, no true officers) -> F skips -> None (D's territory).
    assert route(conn, "ywcc officers") is None


@pytest.mark.parametrize("q", ["president", "vice president", "provost"])
def test_office_slug_words_not_branch2(conn, q):
    # president/vice president -> org 52, provost -> org 53 (non-root) -> excluded from F.
    assert _skill(route(conn, q)) != "ambiguous_officers"


# ── branch 1: role vocab, org-less -> people_by_role(role, None) ──────────────────
@pytest.mark.parametrize("q,role", [("dean", "dean"), ("director", "director"),
                                    ("coordinator", "coordinator")])
def test_branch1_orgless_role_words_route_people_by_role(conn, q, role):
    r = route(conn, q)
    assert _skill(r) == "people_by_role"
    assert r.args.get("role_head") == role and r.args.get("org_id") is None


def test_branch1_cfo_synonym(conn):
    r = route(conn, "cfo")
    assert _skill(r) == "people_by_role" and r.args.get("role_head") == "chief financial officer"


def test_branch1_chancellor_routes_even_with_zero_holders(conn):
    # route() returns the Route regardless of holder count; empty->RAG happens at the answer layer.
    r = route(conn, "chancellor")
    assert _skill(r) == "people_by_role" and r.args.get("role_head") == "chancellor"


# ── branch 1 stays strict org-is-None: njit dean is untouched (earlier branch) ───
def test_njit_dean_unchanged_not_F(conn):
    # role branch's bare-org fallback already routes it; F (end of route) never sees it.
    r = route(conn, "njit dean")
    assert _skill(r) == "people_by_role"      # NOT ambiguous_officers, NOT None


# ── residue / process / definitional guards -> F must NOT fire ───────────────────
@pytest.mark.parametrize("q", ["what is a dean", "president office hours",
                               "how to impeach the president", "officer training program",
                               "money", "fund"])
def test_guards_do_not_fire_F(conn, q):
    r = route(conn, q)
    assert _skill(r) not in ("ambiguous_officers",), f"F wrongly fired on {q!r}"
    # these are all None today; F must keep them None
    assert r is None


# ── F4: mutual exclusion — a both-tokens query fires neither branch ───────────────
def test_director_secretary_fires_neither_branch(conn):
    # strip DISPATCH regex only: role 'director' stripped -> 'secretary' is non-stopword residue.
    assert route(conn, "director secretary") is None


# ── answer layer: ambiguous_officers wired at three sites ────────────────────────
def test_ambiguous_officers_run_and_format(conn):
    res = sa.run(conn, Route("ambiguous_officers", {}))
    assert res.get("skill") == "ambiguous_officers"
    assert sa.format_answer(res) == _HINT


def test_ambiguous_officers_is_deterministic(conn):
    assert sa.is_deterministic({"skill": "ambiguous_officers"}) is True


def test_ambiguous_officers_not_resumable():
    assert sa.resumable_action(Route("ambiguous_officers", {})) is None


def test_ambiguous_officers_never_empty(conn):
    # guard the terminal return "" regression (empty -> live-fallback = the bug F kills).
    assert sa.format_answer(sa.run(conn, Route("ambiguous_officers", {}))) != ""


# ── B1: live-path (ROUTER_V21) — the officer catch-set must REACH route() ─────────
@pytest.mark.parametrize("q", ["officers", "treasurer", "secretary", "vp", "executive board",
                               "coordinator", "njit officers"])
def test_fastpath_cue_covers_officer_catchset(q):
    assert _FASTPATH_CUE.search(q) is not None, f"{q!r} would never reach route() on the live path"


class _StubClassifier:
    def ranked(self, message):
        return [("KG", 1.0)]


class _StubIntent:
    def detect(self, message):
        return (None, 0.0)          # not a command intent


@pytest.mark.parametrize("q", ["officers", "treasurer", "secretary", "vp", "executive board",
                               "njit officers"])
def test_live_path_decide_reaches_ambiguous_officers(tmp_path, q):
    db = tmp_path / "f.db"
    c = create_all(str(db))
    _build(c)
    c.close()
    ur = UnifiedRouter(str(db), _StubClassifier(), _StubIntent())
    d = ur.decide(q)
    assert d.family == "KG" and d.skill == "ambiguous_officers", f"{q!r} -> {d}"
