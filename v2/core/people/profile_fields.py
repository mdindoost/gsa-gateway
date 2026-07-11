"""Registry of per-person EXTERNAL PROFILE fields (links + metrics).

The single source of truth for *which* external-profile fields exist and *how* they
render. Storage is a generic bag on the Person node: ``attrs.profiles`` ::

    attrs.profiles = {
        "scholar":  {"url", "citations", "h_index", "i10_index", "updated_at"},
        "linkedin": {"url"},
        "orcid":    {"url"},          # future fields = one row below + the data
        "website":  {"url"},          # alias-reads the crawler's attrs.links.website
    }

Adding a new link field (ORCID, Semantic Scholar, …) = append one ``Field`` row. No
renderer or query changes. This is a flat field *catalog*, not a plugin system: it
scales to more fields of the same two kinds (links, labelled numeric metrics) — not
to arbitrary new render behaviours (time series, co-author graphs are out of scope).

Surfacing (which answer shows what) is NOT decided here — it is encoded by which
structured skill the router picked: the entity card calls :func:`render_links`, the
research-of-person skill calls :func:`render_metrics`, and roster/list answers call
neither. Metrics are rendered DETERMINISTICALLY (never handed to the LLM to restate).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field


@dataclass(frozen=True)
class Metric:
    """One numeric metric and how to render it. ``template`` is a ``str.format`` string
    receiving ``v`` (the value), e.g. ``"{v:,} citations"`` or ``"h-index {v}"``.
    ``aliases`` are the natural-language words a question uses to name this metric — the
    SINGLE source of truth the router matches against (so a new metric is askable + rankable
    purely by being registered)."""

    key: str
    template: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Field:
    """One external-profile field. ``attrs_fallback`` lists alternate dotted paths under
    the node's ``attrs`` to read the URL from (used so ``website`` keeps reading the
    crawler's ``attrs.links.website`` without a migration)."""

    key: str
    label: str
    icon: str
    metrics: tuple[Metric, ...] = ()
    attrs_fallback: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()   # natural-language names for "X's <field>" link queries


# THE registry. Append a row to add a field.
PROFILE_FIELDS: tuple[Field, ...] = (
    Field("scholar", "Google Scholar", "🎓",
          metrics=(Metric("citations", "{v:,} citations",
                          aliases=("citation", "citations", "cited")),
                   Metric("h_index", "h-index {v}",
                          aliases=("h-index", "h index", "hindex")),
                   Metric("i10_index", "i10-index {v}",
                          aliases=("i10-index", "i10 index"))),
          aliases=("google scholar", "scholar", "gscholar")),
    Field("linkedin", "LinkedIn", "💼", aliases=("linkedin", "linked in")),
    Field("orcid", "ORCID", "🔗", aliases=("orcid",)),
    Field("github", "GitHub", "💻", aliases=("github", "git hub")),
    Field("website", "Website", "🌐", attrs_fallback=("website", "links.website"),
          aliases=("website", "web page", "webpage", "homepage", "home page",
                   "personal site", "personal page", "personal website")),
)


def _dig(attrs: dict, dotted: str):
    cur = attrs
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _field_entry(attrs: dict, f: Field) -> dict:
    profiles = attrs.get("profiles") if isinstance(attrs, dict) else None
    entry = (profiles or {}).get(f.key)
    return entry if isinstance(entry, dict) else {}


def _field_url(attrs: dict, f: Field):
    url = _field_entry(attrs, f).get("url")
    if not url:
        for fb in f.attrs_fallback:
            url = _dig(attrs, fb)
            if url:
                break
    return url or None


# A pseudo-field for the FacultyFolio page. Deliberately NOT in PROFILE_FIELDS (it must never
# appear in the normal multi-link line or be matched by match_link_field) — it exists only to
# reuse _field_url's attrs.profiles.facultyfolio.url read.
_FACULTYFOLIO = Field("facultyfolio", "FacultyFolio", "📄")


def facultyfolio_url(attrs: dict | None) -> str | None:
    """The person's FacultyFolio page URL (``attrs.profiles.facultyfolio.url``) or None."""
    return _field_url(attrs or {}, _FACULTYFOLIO)


def render_links(attrs: dict | None, name: str | None = None) -> str | None:
    """A one-line Markdown list of the person's profile links, or None if they have none.
    Driven by the registry — registering a field auto-includes it here.

    Special case: when the person has a published FacultyFolio page, return ONLY a single link
    to that page (it already aggregates their Scholar/LinkedIn/etc.), with a note. The note drops
    the publications/citations claim when the person has no Scholar link (their page renders no
    such section) — honest-partial."""
    attrs = attrs or {}
    folio_url = facultyfolio_url(attrs)
    if folio_url:
        label = f"{name}'s FacultyFolio page" if name else "FacultyFolio page"
        has_scholar = bool(_dig(attrs, "profiles.scholar.url"))
        tail = ("all their links, publications & citation stats in one place"
                if has_scholar else "all their profile links in one place")
        return f"📄 [{label}]({folio_url}) — {tail}"
    parts = []
    for f in PROFILE_FIELDS:
        url = _field_url(attrs, f)
        if url:
            parts.append(f"{f.icon} [{f.label}]({url})")
    return " · ".join(parts) if parts else None


def _fmt_metric(m: Metric, v) -> str:
    try:
        return m.template.format(v=v)
    except (ValueError, KeyError, TypeError):
        return f"{v}"


def render_metrics(attrs: dict | None, only: str | None = None) -> str | None:
    """A one-line deterministic summary of the person's numeric metrics (citations,
    h-index, …), or None if they have none. Never hallucinated — read straight from
    ``attrs.profiles``. ``only`` restricts the output to a single metric key (e.g.
    ``"citations"``) so a metric question can render just the asked number."""
    attrs = attrs or {}
    lines = []
    for f in PROFILE_FIELDS:
        if not f.metrics:
            continue
        entry = _field_entry(attrs, f)
        present = [(m, entry.get(m.key)) for m in f.metrics
                   if entry.get(m.key) is not None and (only is None or m.key == only)]
        if not present:
            continue
        body = ", ".join(_fmt_metric(m, v) for m, v in present)
        updated = entry.get("updated_at")
        suffix = f" — as of {updated}" if updated else ""
        lines.append(f"{f.label}: {body}{suffix}")
    return " · ".join(lines) if lines else None


def metric_fields() -> list[tuple[str, Metric]]:
    """Every (field_key, Metric) across the registry — the catalog the router/skills use
    to know which metrics exist and their JSON path (``profiles.<field_key>.<metric.key>``)."""
    return [(f.key, m) for f in PROFILE_FIELDS for m in f.metrics]


# Pre-compiled (alias -> (field_key, Metric)) word-boundary matchers, longest alias first so a
# multi-word alias ("i10 index") wins over any shorter substring. Anchored on \b so "i10-index"
# never fires inside "i1000".
_METRIC_MATCHERS: tuple[tuple[re.Pattern, str, "Metric"], ...] = tuple(
    (re.compile(r"\b" + re.escape(alias) + r"\b", re.I), fk, m)
    for fk, m in sorted(metric_fields(),
                        key=lambda fm: max(len(a) for a in fm[1].aliases) if fm[1].aliases else 0,
                        reverse=True)
    for alias in sorted(m.aliases, key=len, reverse=True)
)


def match_metric(text: str) -> tuple[str, Metric] | None:
    """The first metric named in ``text`` as (field_key, Metric), else None. Registry-driven:
    only the aliases registered on a Metric match, so non-metric uses of a word ("how do I cite
    a paper", immigration "form i10") do not match (those aliases are deliberately not registered)."""
    for rx, fk, m in _METRIC_MATCHERS:
        if rx.search(text):
            return (fk, m)
    return None


# (alias -> Field) word-boundary matchers for profile LINK queries ("oria linkedin"), longest alias
# first so "google scholar" wins over a bare "scholar" and "linked in" isn't shadowed.
_LINK_MATCHERS: tuple[tuple[re.Pattern, "Field"], ...] = tuple(
    (re.compile(r"\b" + re.escape(alias) + r"\b", re.I), f)
    for f in sorted(PROFILE_FIELDS,
                    key=lambda f: max((len(a) for a in f.aliases), default=0), reverse=True)
    for alias in sorted(f.aliases, key=len, reverse=True)
)


def match_link_field(text: str) -> tuple[str, "Field"] | None:
    """The first profile-link field named in ``text`` as (field_key, Field), else None — for
    "<person> linkedin / scholar / github / website" queries. Registry-driven via Field.aliases."""
    for rx, f in _LINK_MATCHERS:
        if rx.search(text):
            return (f.key, f)
    return None
