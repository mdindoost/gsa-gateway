"""Mechanical formatters — pure, input-agnostic, no maintained lookup tables.

Every transform here must run on a string it has never seen, with no dictionary,
and produce a defensible result (spec §3.4, §7). Editorial curation is forbidden.
"""
import re

_YEAR = re.compile(r"\b(\d{4})\b")
_ACRONYM = re.compile(r"\(([A-Z]{2,})\)")
_COURSE = re.compile(r"([A-Z]{2,4})\s?(\d{3})")


def clean_mojibake(s: str) -> str:
    """Strip the U+FFFD replacement char and collapse whitespace."""
    if not s:
        return ""
    s = s.replace("�", "")
    return re.sub(r"\s+", " ", s).strip()


def normalize_name(s: str) -> str:
    """'Koutis, Ioannis' -> 'Ioannis Koutis'. Single comma only; else verbatim."""
    if not s:
        return ""
    m = re.fullmatch(r"([^,]+),\s+([^,]+)", s.strip())
    return f"{m.group(2)} {m.group(1)}" if m else s.strip()


def initials(name: str) -> str:
    toks = [t for t in normalize_name(name).split() if t]
    if not toks:
        return ""
    if len(toks) == 1:
        return toks[0][0].upper()
    return (toks[0][0] + toks[-1][0]).upper()


# Common English function-words: short + all-caps in SHOUTING input but NOT acronyms.
# Standard title-case convention (linguistic normalization, not domain curation).
_FUNCTION_WORDS = {
    "to", "and", "of", "the", "a", "an", "in", "on", "for", "at", "by", "or",
    "as", "is", "vs", "with", "from", "into",
}


def smart_titlecase(s: str) -> str:
    """Title-case, but keep a short all-caps token as an acronym (AI, ML) unless
    it is a common function-word (TO, AND) which title-cases normally.

    Cannot mechanically tell an acronym (AI) from a truncated word (ADV/DES);
    the latter stay shouty — the accepted honest-but-ugly cost (spec §7, Fable).
    """
    out = []
    for tok in s.split():
        if tok.isupper() and len(tok) <= 3 and tok.lower() not in _FUNCTION_WORDS:
            out.append(tok)          # acronym / abbreviation -> keep
        else:
            out.append(tok.title())  # normal word (incl. known short words) -> title-case
    return " ".join(out)


def commafy(n) -> str:
    return f"{int(n):,}"


def format_venue(raw: str) -> str:
    """Mechanical venue: acronym-or-fragment + year. No venue dictionary (spec §7)."""
    s = clean_mojibake(raw)
    if not s:
        return ""
    years = _YEAR.findall(s)
    year = years[-1] if years else ""
    if "arxiv" in s.lower():
        return f"arXiv {year}".strip()
    m = _ACRONYM.search(s)
    if m:
        return f"{m.group(1)} {year}".strip()
    seg = s.split(",")[0]
    seg = re.sub(r"\d+(st|nd|rd|th)\s+Annual", "", seg, flags=re.I)
    seg = re.sub(r"IEEE\s+Symposium\s+on", "", seg, flags=re.I)
    seg = re.sub(r"Proceedings of the", "", seg, flags=re.I)
    seg = re.sub(r"\b\d{4}\b", "", seg)            # drop embedded years (avoid double)
    seg = re.sub(r"\s+", " ", seg).strip(" .,-–—…")
    return f"{seg} {year}".strip()


def format_office(raw: str) -> str:
    """'4105 Guttenberg ... (GITC)' -> '4105 GITC'. Mechanical acronym rule."""
    s = clean_mojibake(raw)
    if not s:
        return ""
    m = _ACRONYM.search(s)
    if m:
        room = s.split()[0]
        return f"{room} {m.group(1)}".strip()
    return s


def _norm_code(code: str) -> str:
    m = _COURSE.search(code)
    return f"{m.group(1)} {m.group(2)}" if m else code.strip()


def _course_num(code: str) -> int:
    m = _COURSE.search(code)
    return int(m.group(2)) if m else 0


def format_teaching_interests(raw: str) -> list:
    """Extract the free-text 'Teaching Interests;' section the crawler emits
    (distinct from 'Past Courses;'). Mechanical: locate the literal section
    label, take up to the next section marker, comma-split, acronym-preserving
    title-case. Returns [] when no interests section is present.
    """
    s = clean_mojibake(raw)
    m = re.search(r"Teaching Interests;?\s*(.*?)(?:;?\s*Past Courses\b|$)",
                  s, flags=re.I | re.S)
    if not m:
        return []
    body = m.group(1).strip().strip(";").strip()
    if not body:
        return []
    items = [smart_titlecase(p.strip()) for p in body.split(",")]
    return [i for i in items if i]


def format_teaching(raw: str) -> list:
    """Two-pass mechanical grouping (spec §7 B4).

    Strip provenance/'Past Courses' prefixes; parse (code, title) pairs; Pass 1
    collapses title-variants per FULL code (CS 675 != DS 675) to the longest title;
    Pass 2 groups cross-listings by title. Entries ordered by lowest course number.
    """
    s = clean_mojibake(raw)
    s = re.sub(r"^Courses taught by .*?:\s*", "", s)
    s = re.sub(r"^Past Courses;?\s*", "", s, flags=re.I)
    pairs = re.findall(r"([A-Z]{2,4}\s?\d{3}):\s*(.*?)(?=\s+[A-Z]{2,4}\s?\d{3}:|$)", s)

    # Pass 1: per full code, keep the longest cleaned title (tie -> lexicographic).
    per_code = {}
    for code, title in pairs:
        code = _norm_code(code)
        title = re.sub(r"^ST:?\s*", "", title.strip())
        title = smart_titlecase(title).strip()
        if not title:
            continue
        cur = per_code.get(code)
        if cur is None:
            per_code[code] = title
        else:
            # longest title wins; tie -> lexicographically first (deterministic)
            per_code[code] = min([cur, title], key=lambda t: (-len(t), t))

    # Pass 2: group codes by canonical title.
    by_title = {}
    for code, title in per_code.items():
        by_title.setdefault(title.casefold(), {"title": title, "codes": []})["codes"].append(code)

    entries = []
    for grp in by_title.values():
        codes = sorted(grp["codes"], key=lambda c: (_course_num(c), c))
        label = grp["title"]
        if len(codes) > 1:
            label = f"{label} ({' / '.join(codes)})"
        entries.append((min(_course_num(c) for c in codes), label))
    entries.sort(key=lambda e: e[0])
    return [label for _, label in entries]


_BARE_YEAR = re.compile(r"^\s*\d{4}\s*$")
_LEAD_YEAR = re.compile(r"^\s*(\d{4})")


def format_awards(titles) -> list:
    """Crawled award strings (from the `award.title` column) → clean verbatim list.

    Mechanical de-noise (Option A): drop rows that are a bare year (`^\\d{4}$` — an orphan
    fragment the crawler's 2-column table split produced, whose year already appears in the
    sibling full-award row); strip trailing separators; dedup case-insensitively. Order by
    descending leading year (stable → idempotent), yearless rows kept in source order last.
    Everything else passes through VERBATIM (no rewording).
    """
    cleaned, seen = [], set()
    for t in titles or []:
        t = clean_mojibake(t or "")
        t = t.strip().strip(".,;·–—-").strip()      # strip trailing/leading punct (incl '.') FIRST
        if not t or _BARE_YEAR.match(t):            # so "2019" and "2019." both drop as noise
            continue
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            cleaned.append(t)
    def _yk(t):
        m = _LEAD_YEAR.match(t)
        return (0, -int(m.group(1))) if m else (1, 0)   # yeared first (desc), yearless last
    return sorted(cleaned, key=_yk)


def format_service(raw: str) -> str:
    """Crawled service prose → the person's service text, verbatim, with only the crawler's
    structural lead-in removed (`Service (of|by) <name>[ (dept)]:`), strip-to-first-colon so
    a missing dept parenthetical still strips. Returns '' when empty."""
    s = clean_mojibake(raw or "")
    s = re.sub(r"^Service (of|by) [^:]{1,160}:\s*", "", s)
    return s.strip()


_RS_PROVENANCE = re.compile(r"^Research statement of [^:]{1,160}:\s*")
# The interests section, bounded by the next NJIT structural marker. Markers are the crawler's own
# Title-Case section labels (njit_adapter _GRANT_LABELS = {"in progress","completed"} + "Patents");
# case-sensitive so a lowercase 'patents'/'in progress' inside a sentence is NOT a boundary.
_RS_SECTION = re.compile(
    r"Research Interests[:.]?\s*(.*?)(?:\s*(?:In Progress|Completed|Patents)\b|$)", re.S)


def clean_research_statement(raw) -> str:
    """Extract ONLY the 'Research Interests' section from the crawler's fused `research_statement`.

    The stored blob concatenates several NJIT profile sections with NO separators
    (Research Interests / In Progress / Completed / Patents). Take the interests section up to the
    next structural marker (same shape as `format_teaching_interests` bounding at 'Past Courses').
    No interests label ⇒ '' (patents/grants-only rows omit the row). Verbatim otherwise; the label
    strip is single-shot (first-match), so a body starting 'Research interests are…' is preserved.
    Mechanical; no lookup table (base spec §3.4).
    """
    s = clean_mojibake(raw or "")
    s = _RS_PROVENANCE.sub("", s)
    m = _RS_SECTION.search(s)
    return m.group(1).strip() if m else ""


_EDU_TAIL_YEAR = re.compile(r",?\s*((?:19|20)\d{2})\.?\s*$")
_BARE_YEAR_FIELD = re.compile(r"^\s*(?:19|20)\d{2}\s*$")


def format_education(raw: str) -> list:
    """Parse the crawler's education blob into per-degree lines. NJIT emits TWO layouts, told
    apart purely by YEAR STRUCTURE (no degree vocabulary — must generalize to any dept/college):

    A) component-style — a bare 4-digit-year ';'-field closes a record whose earlier ';'-fields
       are (degree; institution; field…): "Ph.D.; Ben-Gurion University; CS; 2016; M.Tech.; …".
    B) per-degree — each ';'-field is ONE full degree with the year embedded at the end:
       "Diplôme d'ingénieur, Institut …, 1989; Ph.D. in CS, École …, France, 1994." (Vincent Oria).

    Discriminator (generalizable): a bare-year field ⇒ A. Else, only fields carrying an embedded
    trailing year are emitted as degrees ⇒ B. A record with NO year signal at all (yearless
    component rosters — Fox/Rahman) yields [] (honest-empty) rather than orphan institution lines.
    Mechanical + verbatim; no maintained lookup table (base spec §3.4).
    """
    s = clean_mojibake(raw)
    s = re.sub(r"^Education of .*?:\s*", "", s)
    fields = [f.strip() for f in s.split(";") if f.strip()]

    if any(_BARE_YEAR_FIELD.match(f) for f in fields):          # layout A (component-style)
        out, buf = [], []
        for f in fields:
            if _BARE_YEAR_FIELD.match(f):
                year = f.strip()
                if len(buf) >= 2:
                    degree, institution = buf[0], buf[1]
                    field = ", ".join(buf[2:])
                    head = f"{degree} {field}" if field else degree
                    out.append(f"{head}, {institution} ({year})")
                buf = []
            else:
                buf.append(f)
        return out

    # layout B — emit ONLY fields that are "content + embedded trailing year" (Degree …, YYYY).
    # A field with no trailing year is ambiguous (could be an orphan institution) → skipped, so a
    # fully-yearless record yields [] not garbage. Year-structure only — no degree word list.
    out = []
    for f in fields:
        m = _EDU_TAIL_YEAR.search(f)
        if m and not _BARE_YEAR_FIELD.match(f):
            body = f[:m.start()].rstrip(" ,.")
            if body:
                out.append(f"{body} ({m.group(1)})")
    return out
