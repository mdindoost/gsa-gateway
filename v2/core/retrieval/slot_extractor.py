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

import re
from dataclasses import dataclass, field

from v2.core.people import profile_fields
from v2.core.retrieval import entity, skills
from v2.core.retrieval import router as srouter
from v2.core.retrieval.router import Route

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
    r"for (?:that|the other|this) one|the (?:former|latter))\b", re.I)


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
    "(rank people by a metric — metric required, org optional; set order=desc for least/fewest)."
)

# PINNED few-shot — drawn from TRAIN-split intuition; deliberately uses entities/paraphrases NOT in
# the 97-row blind test set (no test-selection or entity leakage into the extractor prompt).
_FEWSHOT = [
    ('which prof does ML in computing',
     {"skill": "people_by_research_area", "slots": {"area": "machine learning", "org": "computing"}, "confidence": 0.9}),
    ('can you tell me a bit about professor Koutis?',
     {"skill": "entity_card", "slots": {"person": "Koutis"}, "confidence": 0.95}),
    ('I am trying to reach someone named Koutis',
     {"skill": "entity_card", "slots": {"person": "Koutis"}, "confidence": 0.8}),
    ('how do I apply for a travel award',
     {"skill": "none", "slots": {}, "confidence": 0.9}),
    ('who leads the math department',
     {"skill": "people_by_role", "slots": {"role": "chair", "org": "math"}, "confidence": 0.85}),
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
    for k in ("person", "org", "area", "metric", "profile", "role", "order"):
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


def _identity_cued(message: str, name: str) -> bool:
    """True iff `message` is a genuine identity ask for this person: an explicit person-intent/attr
    cue, OR the query minus filler minus the person's own name tokens is empty (i.e. essentially just
    the name). A leftover foreign content token ('mmi mohammad dindoost') → False. This is looser than
    route()'s cue list ON PURPOSE — catching paraphrases route() misses is the point — while still
    refusing to card-answer a fragment that merely contains a name."""
    q = message.strip().lower().rstrip("?").strip()
    if srouter._PERSON_INTENT.search(q) or srouter._PERSON_ATTR.search(q):
        return True
    name_toks = set(srouter._qtokens(name))
    residual = [t for t in srouter._qtokens(q) if t not in _IDENTITY_FILLER and t not in name_toks]
    return not residual


def _resolve_person_slot(conn, person: str):
    """('ok', entity_id, name) | ('ambiguous', candidates) | ('none',)."""
    hits = entity.resolve_people(conn, person)
    if not hits and len(person.split()) == 1:
        hits = entity.persons_by_lastname(conn, person)
    if len(hits) == 1:
        return ("ok", hits[0]["entity_id"], hits[0]["name"])
    if len(hits) >= 2:
        return ("ambiguous", hits)
    return ("none",)


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

    q = message.strip().lower()

    def org_id_from_slot():
        oid, _phrase = srouter._find_org(conn, slots["org"].lower()) if "org" in slots else (None, None)
        return oid

    # person-centric
    if skill in ("entity_card", "research_of_person"):
        st = _resolve_person_slot(conn, slots["person"])
        if st[0] == "ambiguous":
            return Route("person_disambig", {"candidates": st[1]})
        if st[0] != "ok":
            return None
        # entity_card is the bare-identity catch-all → guard against firing on a fragment that only
        # INCIDENTALLY contains a name ("Mmi mohammad dindoost"): require an identity cue OR that the
        # query is essentially just the name — the exact condition route() uses (router.py:545).
        if skill == "entity_card" and not _identity_cued(message, st[2]):
            return None
        return Route(skill, {"entity_id": st[1], "name": st[2]})

    if skill == "people_by_name":
        if entity.resolve_people(conn, slots["person"]):
            return Route("people_by_name", {"name": slots["person"]})
        return None

    if skill == "metric_of_person":
        mk = slots["metric"]
        if mk not in _SCHOLAR_METRIC_KEYS:
            return None
        if slots.get("order") == "desc":
            return Route("metric_descending_unsupported", {"field_key": "scholar", "metric_key": mk})
        st = _resolve_person_slot(conn, slots["person"])
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
        st = _resolve_person_slot(conn, slots["person"])
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
        target_org = org_id_from_slot()
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
        if slots.get("order") == "desc":
            return Route("metric_descending_unsupported", {"field_key": "scholar", "metric_key": mk})
        oid = org_id_from_slot()
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
        oid = org_id_from_slot()
        if oid is None:                          # bare area — require KG existence
            if skills.count_people_by_research_area(conn, area, None) < _min_area_support(conn):
                return None
        return Route(skill, {"area": area, "org_id": oid})

    # org-only skills (with route()'s negative guards replicated)
    if skill in ("faculty_in_department", "people_in_org", "officers_in_org", "areas_in_org",
                 "area_counts", "faculty_areas_in_department", "org_departments"):
        oid = org_id_from_slot()
        if oid is None:
            return None
        if skill == "org_departments" and not srouter._has_child_departments(conn, oid):
            return None
        if skill == "people_in_org" and srouter._is_university_root(conn, oid):
            return None
        return Route(skill, {"org_id": oid})

    return None
