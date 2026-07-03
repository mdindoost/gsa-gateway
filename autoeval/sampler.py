# autoeval/sampler.py
from __future__ import annotations
import random, sqlite3, sys
from pathlib import Path
from autoeval.models import SourceItem

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval import entity as _entity   # reused, read-only
from v2.core.retrieval import skills as _skills

_PERSON_CONTACT = ("email", "phone", "office")

def extract_person(conn: sqlite3.Connection, key: str) -> SourceItem:
    contact = _entity.contact_of_person(conn, key)      # {name,email,phone,office,present}
    titles = _entity.title_of_person(conn, key)["titles"]  # [(title, org)]
    research = _entity.research_of_person(conn, key)["areas"]
    attrs = _entity.person_attrs(conn, key)
    scholar = ((attrs.get("profiles") or {}).get("scholar") or {})
    gt = {"name": contact["name"], "email": contact["email"], "phone": contact["phone"],
          "office": contact["office"], "titles": titles, "research_areas": research,
          "scholar": {k: scholar.get(k) for k in ("citations", "h_index", "i10_index")}}
    has = list(contact["present"])
    if titles: has.append("titles")
    if research: has.append("research_areas")
    if any(gt["scholar"].values()): has.append("scholar")
    all_fields = list(_PERSON_CONTACT) + ["titles", "research_areas", "scholar"]
    missing = [f for f in all_fields if f not in has]
    return SourceItem(item_type="person", item_key=key, display_name=contact["name"],
                      ground_truth=gt, has_fields=has, missing_fields=missing)

def extract_org(conn: sqlite3.Connection, org_id: int) -> SourceItem:
    # Ground truth carries ONLY facts a real user would ask about (name, type, members).
    # KG-internal structure (aliases, field lists, ids) is deliberately EXCLUDED — feeding it to
    # the generator produced junk meta-questions ("how many aliases", "which fields are available")
    # that no student asks and that Kavosh has no skill for, inflating routing_failure with noise.
    row = conn.execute("SELECT name,type FROM organizations WHERE id=?", (org_id,)).fetchone()
    members = _skills.people_in_org(conn, org_id)        # [(name,title,email)]
    gt = {"name": row["name"], "type": row["type"], "members": [m[0] for m in members]}
    has = ["name", "type"] + (["members"] if members else [])
    missing = ["members"] if not members else []
    return SourceItem(item_type="org", item_key=str(org_id), display_name=row["name"],
                      ground_truth=gt, has_fields=has, missing_fields=missing)

def _person_keys(conn, limit):
    return [r["key"] for r in conn.execute(
        "SELECT key FROM nodes WHERE type='Person' AND is_active=1 ORDER BY key LIMIT ?",
        (limit,)).fetchall()]

def _org_ids(conn, limit):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM organizations WHERE is_active=1 ORDER BY id LIMIT ?", (limit,)).fetchall()]

# DEFERRED: area/chunk extractors. Their mix fractions are absorbed by person+org below
# (org count still drawn from mix.get("org"), then persons fill the ENTIRE remainder) so a
# run still returns exactly n items even though area/chunk aren't implemented yet.
def sample_items(conn: sqlite3.Connection, mix: dict, n: int,
                 prefer_keys: list[str] | None = None, seed: int | None = None) -> list[SourceItem]:
    """Sample n items across types by `mix`. `prefer_keys` (from coverage) biases toward
    least-tested items so a long run sweeps the whole DB. Person + Org implemented here;
    area/chunk fall back to person until their extractors land (see Task 4b note)."""
    rng = random.Random(seed)
    out: list[SourceItem] = []
    oids = _org_ids(conn, 2000); rng.shuffle(oids)
    n_org = min(int(round(n * mix.get("org", 0.2))), len(oids))
    orgs_drawn = oids[:n_org]
    for oid in orgs_drawn:
        out.append(extract_org(conn, oid))
    n_person = n - len(orgs_drawn)
    pkeys = _person_keys(conn, 5000)
    if prefer_keys:
        pref = [k for k in prefer_keys if k in set(pkeys)]
        pkeys = pref + [k for k in pkeys if k not in set(pref)]
    else:
        rng.shuffle(pkeys)
    for k in pkeys[:n_person]:
        out.append(extract_person(conn, k))
    return out
