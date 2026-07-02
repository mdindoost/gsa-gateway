"""Constrained-JSON slot-extraction FALLBACK for KG-family queries (Workstream 1).

When the family classifier says KG but the deterministic regex router (`router.route`) extracts no
valid {skill, slots} (returns None), this module is the fallback: a local Granite call constrained to
a JSON schema proposes {skill, slots (natural vocab), confidence}, then `resolve_and_validate` maps
the natural slots to real skill args using the SAME resolvers `router.route` uses — resolving org via
`router._find_org`, metric by `Metric.key`, person via `entity.resolve_people`/`persons_by_lastname`,
role via `router._ROLE_VOCAB` + scope-climb — and NEVER executes a skill with an unvalidated slot
(unresolved ⇒ person_disambig for people, else abstain⇒None⇒RAG). Design:
docs/superpowers/specs/2026-07-01-constrained-json-slot-extraction-design.md (rev2, Option A).

No family/retrieval/generation code is touched. The LLM emits surface strings only; the KG decides
what is real.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from v2.core.people import profile_fields
from v2.core.retrieval import entity, skills
from v2.core.retrieval import router as srouter
from v2.core.retrieval.router import Route
from v2.core.retrieval.skills import ORG_TYPE_ENUM


def _mention_grounded(mention: str, message: str) -> bool:
    """True iff the extractor's SURFACE person mention actually appears in the query (fuzzy token
    containment). Anti-hallucination: Granite sometimes invents a name from the few-shot ('Email him'
    -> person='Koutis'). Fuzzy (not exact) so a typo the extractor auto-corrected ('kotis'->'Koutis')
    still grounds. Checks the TYPED slot, never the resolved name, so WS2 fuzzy resolution composes."""
    mtoks = srouter._qtokens(mention)
    qtoks = srouter._qtokens(message)
    if not mtoks:
        return False
    for mt in mtoks:
        for qt in qtoks:
            if mt == qt:
                return True
            # containment only for tokens long enough that a match is a real name fragment, not a
            # short common word ('is'/'he') that happens to be a substring of a longer name token
            # ('koutis') — that false positive let 'What is his position' hallucinate person=Koutis.
            if len(mt) >= 3 and len(qt) >= 3 and (mt in qt or qt in mt):
                return True
            if SequenceMatcher(None, mt, qt).ratio() >= 0.8:
                return True
    return False


_ORG_TYPE_SYNONYMS = {
    "club": re.compile(r"\b(clubs?|student\s+(?:organizations?|orgs?|groups?)|rgos?)\b"),
    "college": re.compile(r"\bcolleges?\b"),
    "department": re.compile(r"\bdepartments?\b"),
}

def _org_type_grounded(org_type: str, message: str) -> bool:
    """True iff a surface synonym of org_type appears in the query — blocks Granite coercing an
    off-enum word to the nearest valid enum ('List the schools' -> org_type='college')."""
    rx = _ORG_TYPE_SYNONYMS.get(org_type)
    return bool(rx and rx.search(message.lower()))

# Cap the candidate list shown in a fuzzy "did you mean…?" CLARIFY (render sanity).
_MAX_DISAMBIG = 6

# ── Shared skill registry (single source of truth; imported by v2/eval/router/dataset) ──────────
# The skills the EXTRACTOR may emit. `person_disambig` is an OUTCOME (never extractor-emitted) and is
# added by the eval VALID_SKILLS separately. papers_of_person / citation_trend_of_person are DEFERRED
# (0 labeled rows; rejected by VALID_SKILLS) — they stay on regex route() only.
KG_SKILL_NAMES: tuple[str, ...] = (
    "entity_card", "research_of_person", "metric_of_person", "link_of_person",
    "people_by_role", "people_by_name", "faculty_in_department", "people_in_org",
    "officers_in_org", "top_people_by_metric", "people_by_research_area",
    "count_people_by_research_area", "areas_in_org", "area_counts",
    "faculty_areas_in_department", "people_by_area_tag", "org_departments",
    "contact_of_person", "title_of_person", "orgs_by_type",
)

# Required natural slots per skill (org/area may be optional where the skill supports it).
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "entity_card": ("person",),
    "research_of_person": ("person",),
    "people_by_name": ("person",),
    "metric_of_person": ("person", "metric"),
    "link_of_person": ("person", "profile"),
    "people_by_role": ("role",),
    "faculty_in_department": ("org",),
    "people_in_org": ("org",),
    "officers_in_org": ("org",),
    "areas_in_org": ("org",),
    "area_counts": ("org",),
    "faculty_areas_in_department": ("org",),
    "org_departments": ("org",),
    "people_by_research_area": ("area",),
    "count_people_by_research_area": ("area",),
    "people_by_area_tag": ("area",),
    "top_people_by_metric": ("metric",),
    "contact_of_person": ("person",),
    "title_of_person": ("person",),
    "orgs_by_type": ("org_type",),
}

_SCHOLAR_METRIC_KEYS = tuple(m.key for fk, m in profile_fields.metric_fields() if fk == "scholar")
_LINK_FIELD_KEYS = tuple(f.key for f in profile_fields.PROFILE_FIELDS)

# Minimum FTS faculty support for a BARE (no-org) area fire to execute — the KG-existence guard that
# stops a hallucinated area ("how do I learn ML") from firing a research-area skill. Settings-tunable.
_DEFAULT_MIN_AREA_SUPPORT = 1

# Anaphoric / context-dependent CONTINUATIONS ("what about X", "who else …", "how about …", "and for
# Y") can't be routed standalone — they need conversation history, so the extractor must NOT mine a
# person/org out of them (hardneg category). Mirrors router.py's process/eligibility guards; caught by
# the hardneg merge gate. Anchored at the start so a mid-sentence "and" doesn't trip it.
_FOLLOWUP_RX = re.compile(
    r"^\s*(?:what about|how about|who else|what else|and (?:for|what|who|how|about)|"
    r"for (?:that|the other|this) one|the (?:former|latter)|"
    r"(?:the\s+)?same\s+(?:question|thing|one))\b", re.I)


@dataclass
class ExtractResult:
    skill: str
    slots: dict = field(default_factory=dict)
    confidence: float = 0.0


# ── JSON schema ────────────────────────────────────────────────────────────────────────────────
def build_schema() -> dict:
    """The strict output schema handed to Ollama's `format`. Enum = KG_SKILL_NAMES + 'none'."""
    return {
        "type": "object",
        "required": ["skill", "slots", "confidence"],
        "properties": {
            "skill": {"type": "string", "enum": list(KG_SKILL_NAMES) + ["none"]},
            "slots": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "person": {"type": "string"},
                    "org": {"type": "string"},
                    "area": {"type": "string"},
                    "metric": {"type": "string", "enum": list(_SCHOLAR_METRIC_KEYS)},
                    "profile": {"type": "string", "enum": list(_LINK_FIELD_KEYS)},
                    "role": {"type": "string"},
                    "order": {"type": "string", "enum": ["asc", "desc"]},
                    "org_type": {"type": "string", "enum": list(ORG_TYPE_ENUM)},
                    "n": {"type": "integer"},
                },
            },
            "confidence": {"type": "number"},
        },
    }


_SYSTEM = (
    "You extract the structured intent of a question about NJIT people, departments, roles, research "
    "areas, and Google-Scholar metrics into JSON. Choose exactly one `skill` from the enum, fill only "
    "the `slots` explicitly present in the question, and give a `confidence` 0..1. Extract surface "
    "strings only — never invent a name, org, or area. If the question is not a precise structured "
    "ask about these, return skill=\"none\". Skills: entity_card (who is X / tell me about X); "
    "research_of_person (what X researches); metric_of_person (X's citations/h-index — needs metric); "
    "link_of_person (X's scholar/linkedin/etc — needs profile); people_by_role (who is the dean/chair/"
    "provost of an org — role required, org optional); people_by_name (list people named X); "
    "faculty_in_department / people_in_org / officers_in_org / areas_in_org / area_counts / "
    "faculty_areas_in_department / org_departments (all need org); people_by_research_area / "
    "count_people_by_research_area / people_by_area_tag (need area, org optional); top_people_by_metric "
    "(rank people HIGHEST-first by a metric — 'most cited', 'top N by h-index'; metric required, org "
    "optional; set order=asc ONLY for a least/fewest/lowest ask, which is not supported)."
    " contact_of_person (X's email/phone/office — needs person); title_of_person (X's title/position "
    "— needs person); orgs_by_type (list/how-many CLUBS or COLLEGES — needs org_type in "
    "{club,college}, org optional as a parent)."
)

# PINNED few-shot — drawn from TRAIN-split intuition; deliberately uses entities/paraphrases NOT in
# the 97-row blind test set (no test-selection or entity leakage into the extractor prompt).
_FEWSHOT = [
    ('which prof does ML in computing',
     {"skill": "people_by_research_area", "slots": {"area": "machine learning", "org": "computing"}, "confidence": 0.9}),
    ('can you tell me a bit about professor Koutis?',
     {"skill": "entity_card", "slots": {"person": "Koutis"}, "confidence": 0.95}),
    ('I am trying to reach someone named Koutis',
     {"skill": "contact_of_person", "slots": {"person": "Koutis"}, "confidence": 0.85}),
    ('how do I apply for a travel award',
     {"skill": "none", "slots": {}, "confidence": 0.9}),
    ('who leads the math department',
     {"skill": "people_by_role", "slots": {"role": "chair", "org": "math"}, "confidence": 0.85}),
    ('what clubs are there at NJIT',
     {"skill": "orgs_by_type", "slots": {"org_type": "club"}, "confidence": 0.9}),
]


def _build_prompt(message: str) -> str:
    import json
    lines = ["Examples:"]
    for q, out in _FEWSHOT:
        lines.append(f"Q: {q}\nJSON: {json.dumps(out)}")
    lines.append(f"\nQ: {message}\nJSON:")
    return "\n".join(lines)


def extract_slots(message: str, generate_json_fn) -> ExtractResult:
    """Call the injected constrained-JSON generator and parse. NEVER raises: any failure (generator
    down, invalid/missing fields, unknown skill) ⇒ ExtractResult('none', {}, 0.0) ⇒ caller falls to
    RAG. `generate_json_fn(system, prompt, schema) -> dict | None`."""
    if _FOLLOWUP_RX.match(message or ""):      # anaphoric follow-up — not standalone-routable
        return ExtractResult("none")
    try:
        raw = generate_json_fn(_SYSTEM, _build_prompt(message), build_schema())
    except Exception:
        return ExtractResult("none")
    if not isinstance(raw, dict):
        return ExtractResult("none")
    skill = raw.get("skill")
    if skill not in KG_SKILL_NAMES:            # includes None / "none" / anything off-registry
        return ExtractResult("none")
    slots = raw.get("slots")
    if not isinstance(slots, dict):
        slots = {}
    try:
        conf = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    # keep only known slot keys, string/int coerced
    clean: dict = {}
    for k in ("person", "org", "area", "metric", "profile", "role", "order", "org_type"):
        v = slots.get(k)
        if isinstance(v, str) and v.strip():
            clean[k] = v.strip()
    if isinstance(slots.get("n"), int):
        clean["n"] = slots["n"]
    return ExtractResult(skill=skill, slots=clean, confidence=conf)


# ── resolve-and-validate: natural slots → real skill args (reuses router's resolvers) ────────────
# Intent/filler words that may surround a name in a genuine identity ask ("can you tell me a bit
# about professor X"). If, after removing these + the person's own name tokens, NOTHING is left, the
# query is essentially just an identity ask; a LEFTOVER content token (e.g. "mmi") means the query is
# about something more than the person → the extractor must NOT card-answer it.
_IDENTITY_FILLER = {
    "can", "could", "would", "you", "u", "please", "pls", "tell", "give", "show", "share", "me",
    "us", "my", "a", "an", "the", "bit", "little", "some", "more", "about", "of", "on", "for", "to",
    "with", "and", "who", "whos", "is", "are", "was", "s", "what", "whats", "do", "does", "know",
    "i", "im", "trying", "try", "want", "wanna", "need", "looking", "look", "find", "reach",
    "contact", "get", "hey", "hi", "someone", "somebody", "person", "people", "named", "name",
    "called", "professor", "prof", "dr", "mr", "ms", "mrs", "info", "information", "details",
    "detail", "profile", "bio", "background", "everything", "anything", "that", "this", "again",
    "just", "kind", "sort", "there",
}


def _identity_cued(message: str, name: str, mention: str = "") -> bool:
    """True iff `message` is a genuine identity ask for this person: an explicit person-intent/attr
    cue, OR the query minus filler minus the person's own name tokens is empty (i.e. essentially just
    the name). A leftover foreign content token ('mmi mohammad dindoost') → False. This is looser than
    route()'s cue list ON PURPOSE — catching paraphrases route() misses is the point — while still
    refusing to card-answer a fragment that merely contains a name.

    ``mention`` is the (possibly typo'd) surface string the person was resolved FROM. On a fuzzy
    resolve the resolved `name` ('Ioannis Koutis') differs from what the user typed ('kotis'), so
    without this the typo token counts as a disqualifying residual and a bare "who is kotis" wrongly
    abstains (WS2 review B2). Passing the mention consumes those tokens too."""
    q = message.strip().lower().rstrip("?").strip()
    if srouter._PERSON_INTENT.search(q) or srouter._PERSON_ATTR.search(q):
        return True
    name_toks = set(srouter._qtokens(name))
    if mention:                                          # fuzzy-resolved: the TYPO'd mention tokens
        name_toks |= set(srouter._qtokens(mention))      # ARE the person reference — consume them too
    residual = [t for t in srouter._qtokens(q) if t not in _IDENTITY_FILLER and t not in name_toks]
    return not residual


def _person_in_org_subtree(conn, person_key: str, org_id: int) -> bool:
    """True iff the person holds an active role anywhere in ``org_id``'s org subtree. The KG-grounded
    corroboration signal (WS2): a fuzzy name candidate is only trusted when the query ALSO names an
    org the candidate really belongs to."""
    subtree = skills.org_descendants(conn, org_id)          # organizations.id set (org + descendants)
    prow = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?", (person_key,)).fetchone()
    if not prow:
        return False
    for (dst,) in conn.execute(
            "SELECT dst_id FROM edges WHERE src_id=? AND type='has_role' AND is_active=1", (prow[0],)):
        row = conn.execute("SELECT attrs FROM nodes WHERE id=? AND type='Org'", (dst,)).fetchone()
        if not row:
            continue
        try:
            dst_oid = (json.loads(row[0]) if row[0] else {}).get("org_id")
        except (TypeError, ValueError):
            dst_oid = None
        if dst_oid in subtree:
            return True
    return False


def _structural_pick(conn, candidates: list[dict], message: str) -> dict | None:
    """WS2 structural corroboration: if the QUERY names an org that exactly ONE fuzzy candidate belongs
    to, that candidate is KG-grounded → safe to auto-resolve. Returns that candidate, else None (⇒ the
    caller CLARIFYs). This is the ONLY way a fuzzy name auto-resolves — never string similarity alone,
    because a 1-edit typo of an absent person is score-indistinguishable from a typo of a present one
    (WS2 review: 'chon'→Chong Jin @89 looks identical to 'kotis'→Koutis @91)."""
    oid, _phrase = srouter._find_org(conn, message.strip().lower())
    if oid is None:
        return None
    matches = [c for c in candidates if _person_in_org_subtree(conn, c["entity_id"], oid)]
    return matches[0] if len(matches) == 1 else None


def _resolve_person_slot(conn, person: str, message: str = ""):
    """('ok', entity_id, name, via) | ('ambiguous', candidates) | ('none',). ``via`` ∈ {'exact','fuzzy'}.

    Tiers: exact (resolve_people) → surname (single token) → FUZZY fallback (WS2). Fuzzy NEVER
    auto-resolves on string similarity alone — it either corroborates structurally (query names a
    matching org ⇒ 'ok', via='fuzzy') or returns 'ambiguous' (⇒ 'did you mean…?' CLARIFY). Clean input
    resolves at the exact tier and never reaches fuzzy, so correctness on clean names is unchanged."""
    hits = entity.resolve_people(conn, person)
    if not hits and len(person.split()) == 1:
        hits = entity.persons_by_lastname(conn, person)
    if len(hits) == 1:
        return ("ok", hits[0]["entity_id"], hits[0]["name"], "exact")
    if len(hits) >= 2:
        return ("ambiguous", hits)
    # exact/surname missed → fuzzy CANDIDATE generation (never a bare resolve)
    fz = entity.fuzzy_people(conn, person)
    if not fz:
        return ("none",)
    top = fz[0]["score"]
    close = [c for c in fz if top - c["score"] <= entity._FUZZY_PERSON_MARGIN]
    picked = _structural_pick(conn, close, message)
    if picked is not None:                               # KG-grounded → safe auto-resolve
        return ("ok", picked["entity_id"], picked["name"], "fuzzy")
    return ("ambiguous", close[:_MAX_DISAMBIG])          # uncorroborated → CLARIFY, never guess


def _min_area_support(conn) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1",
            ("router.slot_min_area_support",)).fetchone()
        return int(row[0]) if row and row[0] is not None else _DEFAULT_MIN_AREA_SUPPORT
    except Exception:
        return _DEFAULT_MIN_AREA_SUPPORT


def resolve_and_validate(conn, skill: str, slots: dict, message: str) -> Route | None:
    """Map extractor natural slots to a real, executable Route — or None to abstain (⇒RAG). Ambiguous
    person ⇒ Route('person_disambig', {candidates}). Never returns a Route with an unresolved slot."""
    if skill not in KG_SKILL_NAMES:
        return None
    for req in REQUIRED_SLOTS.get(skill, ()):        # required slot must be present
        if req not in slots:
            return None

    # anti-hallucination: any person-slot skill must have its person actually named in the query
    if "person" in REQUIRED_SLOTS.get(skill, ()) and not _mention_grounded(slots["person"], message):
        return None

    q = message.strip().lower()

    def resolve_org_slot():
        """(org_id | None, named_but_unresolved). Exact `_find_org` → fuzzy head-word. A True flag
        means an org WAS named but didn't resolve unambiguously (fuzzy multi-match or miss) → the
        caller must ABSTAIN even for org-OPTIONAL skills, never silently default to the root."""
        if "org" not in slots:
            return (None, False)
        oid, _phrase = srouter._find_org(conn, slots["org"].strip().lower())
        if oid is not None:
            return (oid, False)
        fz = srouter.fuzzy_org(conn, slots["org"])
        if len(fz) == 1:                             # single distinct org → KG-grounded resolve
            return (fz[0][0], False)
        return (None, True)                          # 0 or ≥2 distinct → named-but-unresolved

    # person-centric
    if skill in ("entity_card", "research_of_person"):
        st = _resolve_person_slot(conn, slots["person"], message)
        if st[0] == "ambiguous":
            cand_name = st[1][0]["name"] if st[1] else ""
            if not _identity_cued(message, cand_name):
                return None                      # foreign residual ('mmi …') ⇒ fragment, abstain
            return Route("person_disambig", {"candidates": st[1]})
        if st[0] != "ok":
            return None
        # entity_card is the bare-identity catch-all → guard against firing on a fragment that only
        # INCIDENTALLY contains a name ("Mmi mohammad dindoost"): require an identity cue OR that the
        # query is essentially just the name — the exact condition route() uses (router.py:545). On a
        # fuzzy resolve, pass the typo'd mention so it isn't counted as a disqualifying residual.
        mention = slots["person"] if st[3] == "fuzzy" else ""
        if skill == "entity_card" and not _identity_cued(message, st[2], mention):
            return None
        return Route(skill, {"entity_id": st[1], "name": st[2]})

    # WS3 person-attribute skills — inherit WS2 resolution; ambiguous ⇒ person_disambig.
    if skill in ("contact_of_person", "title_of_person"):
        st = _resolve_person_slot(conn, slots["person"], message)
        if st[0] == "ambiguous":
            cand_name = st[1][0]["name"] if st[1] else ""
            if not _identity_cued(message, cand_name):
                return None                      # foreign residual ('mmi …') ⇒ fragment, abstain
            return Route("person_disambig", {"candidates": st[1]})
        if st[0] != "ok":
            return None
        return Route(skill, {"entity_id": st[1], "name": st[2]})

    # WS3 orgs_by_type — validate the type enum; optional parent via the shared org resolver.
    if skill == "orgs_by_type":
        org_type = slots["org_type"]
        if org_type not in ORG_TYPE_ENUM:     # 'school'/anything off-enum ⇒ abstain (never mapped)
            return None
        if not _org_type_grounded(org_type, message):
            return None                          # off-enum coercion ('schools'->college) ⇒ abstain
        parent_id, named_unresolved = resolve_org_slot()
        if named_unresolved:
            return None                       # a parent WAS named but didn't resolve ⇒ abstain
        return Route("orgs_by_type", {"org_type": org_type, "parent_org_id": parent_id})

    if skill == "people_by_name":
        if entity.resolve_people(conn, slots["person"]):
            return Route("people_by_name", {"name": slots["person"]})
        return None

    if skill == "metric_of_person":
        mk = slots["metric"]
        if mk not in _SCHOLAR_METRIC_KEYS:
            return None
        if slots.get("order") == "asc":            # least/fewest — unsupported (asc = lowest-first)
            return Route("metric_descending_unsupported", {"field_key": "scholar", "metric_key": mk})
        st = _resolve_person_slot(conn, slots["person"], message)
        if st[0] == "ok":
            return Route("metric_of_person", {"entity_id": st[1], "name": st[2],
                                              "field_key": "scholar", "metric_key": mk})
        if st[0] == "ambiguous":
            return Route("person_disambig", {"candidates": st[1]})
        return None

    if skill == "link_of_person":
        fk = slots["profile"]
        if fk not in _LINK_FIELD_KEYS:
            return None
        st = _resolve_person_slot(conn, slots["person"], message)
        if st[0] == "ok":
            return Route("link_of_person", {"entity_id": st[1], "name": st[2], "field_key": fk})
        if st[0] == "ambiguous":
            return Route("person_disambig", {"candidates": st[1]})
        return None

    # role lookup (org optional; scope-climb like route())
    if skill == "people_by_role":
        role = srouter._ROLE_SYNONYM.get(slots["role"].lower(), slots["role"].lower())
        if role not in srouter._ROLE_VOCAB:
            return None
        target_org, org_unresolved = resolve_org_slot()
        if org_unresolved:                           # org named but ambiguous/unresolved → abstain
            return None
        if target_org is not None:
            role_level = srouter.ROLE_SCOPE_LEVEL.get(role, 0)
            if role_level > 0:
                row = conn.execute("SELECT type FROM organizations WHERE id=?", (target_org,)).fetchone()
                cur = srouter.ORG_TYPE_LEVEL.get(row[0], 0) if row else 0
                if role_level > cur:
                    climbed = srouter._climb_to_scope(conn, target_org, role_level)
                    if climbed is not None:
                        target_org = climbed
        return Route("people_by_role", {"role_head": role, "org_id": target_org})

    # metric ranking (org optional → university root default)
    if skill == "top_people_by_metric":
        mk = slots["metric"]
        if mk not in _SCHOLAR_METRIC_KEYS:
            return None
        if slots.get("order") == "asc":            # least/fewest — unsupported (asc = lowest-first)
            return Route("metric_descending_unsupported", {"field_key": "scholar", "metric_key": mk})
        oid, org_unresolved = resolve_org_slot()
        if org_unresolved:                           # org named but ambiguous → abstain (not root)
            return None
        args = {"field_key": "scholar", "metric_key": mk, "n": srouter._parse_topn(q)}
        if oid is not None:
            args["org_id"] = oid
            return Route("top_people_by_metric", args)
        root = srouter._root_org_id(conn)
        if root is not None:
            args["org_id"] = root
            args["org_defaulted"] = True
            return Route("top_people_by_metric", args)
        return None

    # area skills (org optional; bare-area needs KG support)
    if skill in ("people_by_research_area", "count_people_by_research_area", "people_by_area_tag"):
        area = slots["area"]
        oid, org_unresolved = resolve_org_slot()
        if org_unresolved:                       # org named but ambiguous → abstain (not bare-area)
            return None
        if oid is None:                          # bare area — require KG existence
            if skills.count_people_by_research_area(conn, area, None) < _min_area_support(conn):
                return None
        return Route(skill, {"area": area, "org_id": oid})

    # org-only skills (with route()'s negative guards replicated)
    if skill in ("faculty_in_department", "people_in_org", "officers_in_org", "areas_in_org",
                 "area_counts", "faculty_areas_in_department", "org_departments"):
        oid, _org_unresolved = resolve_org_slot()
        if oid is None:                          # unresolved/ambiguous/miss → abstain
            return None
        if skill == "org_departments" and not srouter._has_child_departments(conn, oid):
            return None
        if skill == "people_in_org" and srouter._is_university_root(conn, oid):
            return None
        return Route(skill, {"org_id": oid})

    return None
