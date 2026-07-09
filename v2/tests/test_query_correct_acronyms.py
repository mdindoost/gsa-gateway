import pytest

from v2.core.retrieval import skills
from v2.core.database.schema import get_connection
from v2.core.retrieval.query_correct import ACRONYMS, augment_acronyms


def test_augment_keeps_bare_and_appends():
    assert augment_acronyms("dept chair") == "dept department chair"


def test_augment_metric_words():
    # the metric class the dictionary owns (spec §14.1): sci->science, prof->professor
    assert augment_acronyms("top cited prof in computer sci") == \
        "top cited prof professor in computer sci science"


def test_augment_noop_when_no_abbrev():
    assert augment_acronyms("who is the dean of engineering") == "who is the dean of engineering"


def test_augment_case_insensitive_preserves_bare():
    assert augment_acronyms("Which DEPT").lower() == "which dept department"


def test_augment_skips_protected():
    assert augment_acronyms("prof wang", protected={"prof"}) == "prof wang"


def test_org_slug_acronyms_are_not_expanded():
    """Concrete guard for the three tokens the $0 route-diff gate caught (gsa/cs/ece): they must
    pass through UNCHANGED, because the router resolves them natively as org identifiers and
    expanding them demotes a correct structured route into RAG."""
    assert "gsa" not in ACRONYMS
    assert "cs" not in ACRONYMS
    assert "ece" not in ACRONYMS
    assert augment_acronyms("gsa president") == "gsa president"
    assert augment_acronyms("who are the gsa officers") == "who are the gsa officers"
    assert augment_acronyms("who run cs") == "who run cs"
    assert augment_acronyms("ece faculty") == "ece faculty"


@pytest.fixture
def live_conn():
    c = get_connection("gsa_gateway.db")
    yield c
    c.close()


def test_no_acronym_key_shadows_or_splits_an_org_identifier(live_conn):
    """INVARIANT LOCK (Fable N1) — future-proofs the exclusion beyond the three enumerated tokens.

    The acronym dictionary AUGMENTS in place (keeps the bare token, appends the expansion), so a
    key that is ALSO an org identifier — or that sits in a non-final position inside a multi-word
    identifier phrase — will break `resolve_org`/`_find_org`'s native org resolution and silently
    demote a structured route into RAG (the exact bug the route-diff gate caught for `gsa`). This
    test fails the moment a new org (slug/alias) collides with a kept acronym, so the gate that
    caught it once can't be silently reintroduced by a data change. Three legs:
      (i)  no key is itself resolvable as an org (name/slug/_ORG_ALIASES);
      (ii) no key is in the hand-alias map;
      (iii)no key appears as a whole word in a NON-FINAL position of any identifier phrase
           (append-after would split the phrase; a phrase-FINAL key like `sci` in `comp sci`
           is safe because the expansion lands after the whole phrase)."""
    # Identifier phrases the resolver understands: active org names + slugs + hand-alias phrases.
    phrases = set()
    for name, slug in live_conn.execute(
            "SELECT lower(name), lower(slug) FROM organizations WHERE is_active=1"):
        if name:
            phrases.add(name)
        if slug:
            phrases.add(slug)
    phrases.update(a.lower() for a in skills._ORG_ALIASES)

    offenders = []
    for key in ACRONYMS:
        k = key.lower()
        # (i) not itself a resolvable org identifier
        if skills.resolve_org(live_conn, k) is not None:
            offenders.append(f"{k!r} resolves to an org id")
        # (ii) not a hand alias
        if k in skills._ORG_ALIASES:
            offenders.append(f"{k!r} is in _ORG_ALIASES")
        # (iii) not a non-final whole word inside any identifier phrase
        for p in phrases:
            words = p.split()
            for i, w in enumerate(words):
                if w == k and i != len(words) - 1:
                    offenders.append(f"{k!r} is a non-final word in identifier {p!r}")
    assert not offenders, (
        "acronym key(s) collide with an org identifier — expanding them would demote a "
        "structured route into RAG (see §14.1 HARD EXCLUSION): " + "; ".join(offenders))
