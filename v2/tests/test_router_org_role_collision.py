"""D+E — router org/role collision (president) + terse officer forms.

Design: docs/superpowers/specs/2026-07-03-router-org-role-collision-and-terse-officer-design.md

D — a role-word office (slug 'president', org 52) must not steal the org slot from a real named
    org. "who is the gsa president" resolves gsa, not the Office of the President. Gated (gate 3):
    swap only when the alternate org can actually answer the title (officer/deprep edge, or an
    admin edge whose title has 'president' as a segment head) — so "who is the ywcc president"
    does NOT become a mislabeled college roster.
E — terse officer forms ("gsa officers", "gsa president", "gsa treasurer") route to officers_in_org,
    gated to orgs that hold a true officer/deprep role. Guards (title-is-org, zero-residue) keep
    "president office hours" / "former gsa president" out.
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
from v2.core.retrieval.router import route


# ── fixture helpers ─────────────────────────────────────────────────────────────
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


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = _org(c, 1, "NJIT", "njit", "university")
    gsa = _org(c, 2, "Graduate Student Association", "gsa", "custom", parent=1)
    ywcc = _org(c, 4, "Ying Wu College of Computing", "ywcc", "college", parent=1)
    _org(c, 5, "Computer Science", "computer-science", "department", parent=4,
         aliases=["cs", "computer science"])
    gwics = _org(c, 11, "GWICS", "gwics", "custom", parent=1)
    _org(c, 22, "Information Services and Technology", "ist", "office", parent=1, aliases=["ist"])
    _org(c, 24, "Office of the University Registrar", "registrar", "department", parent=1,
         aliases=["registrar", "university registrar"])
    _org(c, 52, "Office of the President", "president", "office", parent=1)

    # graph edges backing the gates
    _role(c, _person(c, "Teik C. Lim", "p:lim"), njit, "admin", ["President"])
    _role(c, _person(c, "Alice Officer", "p:alice"), gsa, "officer", ["President"])
    _role(c, _person(c, "Grace Officer", "p:grace"), gsa, "officer", ["Treasurer"])
    _role(c, _person(c, "Bob Officer", "p:bob"), gwics, "officer", ["President"])
    # YWCC: admin leadership, NO officer/deprep, NO 'president' segment-head title.
    _role(c, _person(c, "Dean Payton", "p:payton"), ywcc, "admin",
          ["Dean, Ying Wu College of Computing"])
    _role(c, _person(c, "Prof Wang", "p:wang"), ywcc, "admin", ["Distinguished Professor"])
    # possessive decoy: 'President's Advisory ...' must NOT satisfy the ^president\b(?!') head match.
    _role(c, _person(c, "Adv Member", "p:adv"), ywcc, "admin", ["President's Advisory Council Member"])
    c.commit()
    yield c
    c.close()


def _skill(r):
    return r.skill if r is not None else None


def _org_of(r):
    return r.args.get("org_id") if r is not None else None


# ── D: verb-ful "who is the <org> president" (officer-identity branch) ───────────
def test_gsa_president_resolves_gsa_not_president_office(conn):
    r = route(conn, "who is the gsa president")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2   # NOT 52


def test_gwics_president_resolves_gwics(conn):
    r = route(conn, "who is the gwics president")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 11


def test_njit_president_resolves_root_via_admin_president_title(conn):
    # root has an admin edge titled 'President' → gate 3 passes on the admin branch.
    r = route(conn, "who is the njit president")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 1


def test_ywcc_president_deflects_not_mislabeled_college_roster(conn):
    # gate 3 FAILS for ywcc (no officer/deprep, no 'president' segment head) → no swap →
    # stays the Office of the President (52, empty) → honest deflect, NOT officers_in_org(4).
    r = route(conn, "who is the ywcc president")
    assert _org_of(r) != 4, "must NOT route to the YWCC college roster"
    assert _skill(r) == "officers_in_org" and _org_of(r) == 52


def test_president_office_hours_no_false_positive(conn):
    # only the president office matches; no distinct alternate → title-is-org guard → RAG.
    assert route(conn, "president office hours") is None


# ── E: terse officer forms (no verb) ────────────────────────────────────────────
def test_terse_gsa_officers(conn):
    r = route(conn, "gsa officers")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_terse_gsa_president(conn):
    r = route(conn, "gsa president")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_terse_gwics_officers(conn):
    r = route(conn, "gwics officers")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 11


def test_terse_gsa_treasurer(conn):
    r = route(conn, "gsa treasurer")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_terse_possessive_gsa_president(conn):
    r = route(conn, "gsa's president")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_terse_ywcc_officers_gate_fails_to_rag(conn):
    # YWCC has no officer/deprep edge → real-officer gate fails → RAG (not a mislabeled roster).
    assert route(conn, "ywcc officers") is None


def test_terse_cs_officers_gate_fails_to_rag(conn):
    assert route(conn, "cs officers") is None


def test_terse_ist_president_deflects(conn):
    # 'ist' office; 'president' is the president office → title-is-org guard on the un-swapped
    # office phrase → no terse fire; no other branch → RAG. Must never route to ist (22).
    r = route(conn, "ist president")
    assert _org_of(r) != 22
    assert r is None


# ── E guards: process / tense / attribute forms must NOT fire ───────────────────
def test_former_gsa_president_does_not_fire(conn):
    # 'former' is non-stopword residue → zero-residue guard blocks → not an officer route.
    assert _skill(route(conn, "former gsa president")) != "officers_in_org"


def test_gsa_president_salary_does_not_fire(conn):
    assert _skill(route(conn, "gsa president salary")) != "officers_in_org"


def test_what_does_gsa_president_do_does_not_fire(conn):
    assert _skill(route(conn, "what does the gsa president do")) != "officers_in_org"


def test_gsa_events_not_hijacked(conn):
    # 'events' is not an officer title → terse officer branch must not fire.
    assert _skill(route(conn, "gsa events")) != "officers_in_org"


# ── regressions: existing routes unchanged ──────────────────────────────────────
def test_verbful_gsa_officers_still_routes(conn):
    r = route(conn, "who are the gsa officers")
    assert _skill(r) == "officers_in_org" and _org_of(r) == 2


def test_njit_registrar_still_routes_to_people_by_role(conn):
    # role-word office 'registrar' must NOT be demoted — D touches only the officer-title collision.
    r = route(conn, "who is the njit registrar")
    assert _skill(r) == "people_by_role" and _org_of(r) == 24


def test_registrar_office_hours_still_rag(conn):
    assert route(conn, "registrar office hours") is None
