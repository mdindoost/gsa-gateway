"""Citations-per-year bar chart — pure function, cites_per_year dict -> SVG string.

Geometry (spec §6): viewBox 0 0 660 158. A 22px top band gives the peak value
label full headroom (no clipping); the plot area is 118px tall on an y=140
baseline, with 18px below for the year axis. The latest year is dimmed as
"partial" ONLY when it equals the sync year, and is excluded from the peak so it
never reads as a decline.
"""

_W = 660.0
_TOPPAD = 22.0      # headroom above the tallest bar for the peak value label
_MAXH = 118.0       # plot height (taller than the original 108)
_BASE = _TOPPAD + _MAXH   # baseline y = 140
_VH = 158.0         # viewBox height: baseline + 18px year-axis band
_AXY = _BASE + 14   # year-axis label baseline
_GAP = 4.0          # gap=4, N=20 -> width 29.2, matching the koutis reference exactly


def _fmt(x: float) -> str:
    """Trim trailing zeros: 29.2 -> '29.2', 8.0 -> '8'."""
    return f"{x:.1f}".rstrip("0").rstrip(".")


def render_chart(cites_per_year: dict, sync_year: int):
    """Return an inline SVG string, or None when the render gate fails
    (a single year — need more than one to show a trajectory — or a
    non-positive peak)."""
    if not cites_per_year:
        return None
    years = sorted(int(y) for y in cites_per_year.keys())
    if len(years) < 2:
        return None
    vals = {int(y): int(v) for y, v in cites_per_year.items()}

    latest = years[-1]
    partial_year = latest if latest == sync_year else None
    full_years = [y for y in years if y != partial_year]
    peak = max((vals[y] for y in full_years), default=0)
    if peak <= 0:
        return None

    n = len(years)
    width = (_W - _GAP * (n - 1)) / n
    step = width + _GAP
    scale = _MAXH / peak

    parts = [
        f'<svg viewBox="0 0 660 {_fmt(_VH)}" role="img" '
        f'aria-label="Citations per year from {years[0]} to {years[-1]}, peaking at {peak}.">',
        f'<line x1="0" y1="{_fmt(_BASE)}" x2="660" y2="{_fmt(_BASE)}" stroke="var(--hair)" stroke-width="1"/>',
    ]
    peak_x = None
    peak_labelled = False
    for i, y in enumerate(years):
        v = vals[y]
        h = v * scale
        x = i * step
        by = _BASE - h
        if y == partial_year:
            cls = "bar partial"
            title = f"{y}: {v} (partial)"
        elif v == peak and not peak_labelled:
            cls = "bar peak"
            title = f"{y}: {v}"
            peak_x = x + width / 2
            peak_labelled = True
        else:
            cls = "bar"
            title = f"{y}: {v}"
        parts.append(
            f'<rect class="{cls}" x="{_fmt(x)}" y="{_fmt(by)}" width="{_fmt(width)}" '
            f'height="{_fmt(h)}" rx="1.5"><title>{title}</title></rect>'
        )

    # axis labels: first (start), peak (centered), last (end)
    parts.append(f'<text class="axl" x="0" y="{_fmt(_AXY)}">{years[0]}</text>')
    if peak_x is not None:
        parts.append(f'<text class="axl" x="{_fmt(peak_x)}" y="{_fmt(_AXY)}" text-anchor="middle">'
                     f'{_peak_year(years, vals, peak, partial_year)}</text>')
    parts.append(f'<text class="axl" x="660" y="{_fmt(_AXY)}" text-anchor="end">{years[-1]}</text>')
    # peak value label above the peak bar (the _TOPPAD band guarantees it clears y=0)
    if peak_x is not None:
        peak_top = _BASE - peak * scale
        parts.append(f'<text class="peaklab" x="{_fmt(peak_x)}" y="{_fmt(peak_top - 6)}" '
                     f'text-anchor="middle">{peak}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _peak_year(years, vals, peak, partial_year):
    for y in years:
        if y != partial_year and vals[y] == peak:
            return y
    return years[-1]
