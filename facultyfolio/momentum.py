"""Citation-momentum — pure functions over a Scholar `cites_per_year` series.

Build-time only; no I/O, no LLM. Turns a `{year: citations_received}` map into a
Theil–Sen growth read used by the ★ Rising leaderboard view and the per-person
"recent trend" line. This module computes numbers + boolean gates ONLY; the honesty
rules (never render a decline, %/yr never bare, no rendered last place) live in the
callers (render/templates). See docs/superpowers/specs/2026-07-08-facultyfolio-rising-momentum-design.md.

Method (pinned by senior-eng review):
- Window = the fixed `MOMENTUM_WINDOW` consecutive complete years `[sync-5 … sync-1]`
  (the person's sync year is partial → excluded). No zero-fill: a missing window year
  fails the gate (guards a future re-crawl; real data has 0 gaps / 0 zero-years today).
- Estimator = Theil–Sen slope (median of the 10 pairwise slopes) of `log1p(cites)`.
  `momentum_pct = round((exp(slope) - 1) * 100)` — the median per-year growth of `1+cites`.
- Membership (who is "rising") = `theil_sen > 0` AND nearest-rank 25th-percentile pairwise
  slope `>= 0` (at most ~25% of year-pairs decline: near-monotone, tolerant of one dip).
"""
from math import log1p, exp, ceil
from statistics import median

from . import config


def window_series(cites_per_year: dict, sync_year: int):
    """The fixed window `[sync-WINDOW … sync-1]` as `(years, values)`, or None when any
    window year is missing (no zero-fill) or inputs are empty. `sync_year` = the year the
    person's Scholar was pulled; that year is the partial/current one and is excluded."""
    if not cites_per_year or not sync_year:
        return None
    vals = {int(y): int(v) for y, v in cites_per_year.items()}
    years = list(range(sync_year - config.MOMENTUM_WINDOW, sync_year))
    if any(y not in vals for y in years):
        return None
    return years, [vals[y] for y in years]


def pairwise_slopes(values) -> list:
    """Sorted list of all pairwise slopes of `log1p(values)` (x = index; window is dense)."""
    lv = [log1p(v) for v in values]
    return sorted((lv[j] - lv[i]) / (j - i)
                  for i in range(len(lv)) for j in range(i + 1, len(lv)))


def theil_sen(values) -> float:
    """Median pairwise slope of `log1p(values)` — the robust per-year log-growth."""
    return median(pairwise_slopes(values))


def p25_nearest_rank(sorted_slopes):
    """Nearest-rank 25th percentile: `k = ceil(0.25 * n)`, take the k-th smallest.
    For n=10 → k=3 → the 3rd-smallest pairwise slope. Deterministic (no interpolation)."""
    k = max(1, ceil(0.25 * len(sorted_slopes)))
    return sorted_slopes[k - 1]


def momentum_pct(values) -> int:
    """Rounded %/yr = `(exp(theil_sen) - 1) * 100`. Growth of `1+cites` (spec N1)."""
    return round((exp(theil_sen(values)) - 1) * 100)


def passes_data_gate(values) -> bool:
    """All window years present (len == WINDOW) AND median citations/yr >= FLOOR."""
    return len(values) == config.MOMENTUM_WINDOW and median(values) >= config.MOMENTUM_FLOOR


def is_rising(values) -> bool:
    """Positive even on the pessimistic read: `theil_sen > 0` AND nearest-rank p25 `>= 0`."""
    slopes = pairwise_slopes(values)
    return median(slopes) > 0 and p25_nearest_rank(slopes) >= 0


def recent_rate(values) -> int:
    """The latest complete window year's citations (the absolute-rate chip)."""
    return values[-1]


def tiny_base(values) -> bool:
    """Latest window year below the tiny-base threshold → show a glyph, not a precise %."""
    return values[-1] < config.MOMENTUM_TINY_BASE


def _fmt_window(years) -> str:
    return f"{years[0]}–{years[-1]}"          # en-dash, e.g. 2021–2025


def _sync_year(scholar_or_row) -> int:
    """int(updated_at[:4]) from a scholar bag or a roster row; 0 when absent/malformed."""
    ua = (scholar_or_row or {}).get("updated_at") or ""
    try:
        return int(str(ua)[:4])
    except (ValueError, TypeError):
        return 0


def recent_trend(cites_per_year: dict, sync_year: int):
    """Per-person profile "recent trend" line (Fable option B). Returns:
      - {"kind":"growing","pct":18,"window":"2021–2025","glyph":False}  (positive & clears noise)
      - {"kind":"growing","glyph":True,"window":"2021–2025"}            (tiny base or rounds <1%)
      - {"kind":"steady"}                                               (flat / mildly-neg / noisy)
      - None                                                            (below the data gate)
    NEVER returns a decline: "steady" is the strongest claim the data licenses for a non-riser."""
    ws = window_series(cites_per_year, sync_year)
    if ws is None:
        return None
    years, values = ws
    if not passes_data_gate(values):
        return None
    if is_rising(values):
        pct = momentum_pct(values)
        if tiny_base(values) or pct < 1:
            return {"kind": "growing", "glyph": True, "window": _fmt_window(years)}
        return {"kind": "growing", "glyph": False, "pct": pct, "window": _fmt_window(years)}
    return {"kind": "steady"}


def rising_view(roster: list):
    """The ★ Rising strip. `roster` = leaderboard rows that MUST carry `cites_per_year`
    + `updated_at` (rank.roster widened). Returns `(rows, funnel)`:
      rows  = risers sorted by momentum desc, each a view-model dict
              {slug,name,title,areas,years,values,window,momentum_pct,recent_rate,glyph}
      funnel= {"risers":R,"gated":G,"scholar":S,"total":T} — all computed, for the caption."""
    total = len(roster)
    scholar = sum(1 for r in roster if r.get("citations") is not None)
    gated = 0
    rows = []
    for r in roster:
        ws = window_series(r.get("cites_per_year") or {}, _sync_year(r))
        if ws is None:
            continue
        years, values = ws
        if not passes_data_gate(values):
            continue
        gated += 1
        if not is_rising(values):
            continue
        pct = momentum_pct(values)
        rows.append({
            "slug": r["slug"], "name": r["name"], "title": r.get("title") or "",
            "areas": r.get("areas") or [],
            "years": years, "values": values, "window": _fmt_window(years),
            "momentum_pct": pct, "recent_rate": recent_rate(values),
            "glyph": tiny_base(values) or pct < 1,
        })
    rows.sort(key=lambda x: (-x["momentum_pct"], (x["name"] or "").casefold(), x["slug"]))
    return rows, {"risers": len(rows), "gated": gated, "scholar": scholar, "total": total}
