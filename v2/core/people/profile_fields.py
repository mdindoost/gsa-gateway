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

from dataclasses import dataclass, field as _dc_field


@dataclass(frozen=True)
class Metric:
    """One numeric metric and how to render it. ``template`` is a ``str.format`` string
    receiving ``v`` (the value), e.g. ``"{v:,} citations"`` or ``"h-index {v}"``."""

    key: str
    template: str


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


# THE registry. Append a row to add a field.
PROFILE_FIELDS: tuple[Field, ...] = (
    Field("scholar", "Google Scholar", "🎓",
          metrics=(Metric("citations", "{v:,} citations"),
                   Metric("h_index", "h-index {v}"),
                   Metric("i10_index", "i10-index {v}"))),
    Field("linkedin", "LinkedIn", "💼"),
    Field("orcid", "ORCID", "🔗"),
    Field("github", "GitHub", "💻"),
    Field("website", "Website", "🌐", attrs_fallback=("website", "links.website")),
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


def render_links(attrs: dict | None) -> str | None:
    """A one-line Markdown list of the person's profile links, or None if they have none.
    Driven by the registry — registering a field auto-includes it here."""
    attrs = attrs or {}
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


def render_metrics(attrs: dict | None) -> str | None:
    """A one-line deterministic summary of the person's numeric metrics (citations,
    h-index, …), or None if they have none. Never hallucinated — read straight from
    ``attrs.profiles``."""
    attrs = attrs or {}
    lines = []
    for f in PROFILE_FIELDS:
        if not f.metrics:
            continue
        entry = _field_entry(attrs, f)
        present = [(m, entry.get(m.key)) for m in f.metrics if entry.get(m.key) is not None]
        if not present:
            continue
        body = ", ".join(_fmt_metric(m, v) for m, v in present)
        updated = entry.get("updated_at")
        suffix = f" — as of {updated}" if updated else ""
        lines.append(f"{f.label}: {body}{suffix}")
    return " · ".join(lines) if lines else None
