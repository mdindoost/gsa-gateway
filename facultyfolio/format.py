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


def format_education(raw: str) -> list:
    """Year-anchored record split (spec §7 B5) — records are variable-length.

    Strip the provenance prefix; accumulate ';'-fields until a bare 4-digit year
    closes a record (degree=first, institution=second, field=the rest). A record
    needs institution AND year to be valid; degree-only (Oria) yields nothing.
    """
    s = clean_mojibake(raw)
    s = re.sub(r"^Education of .*?:\s*", "", s)
    fields = [f.strip() for f in s.split(";") if f.strip()]
    out, buf = [], []
    for f in fields:
        if re.fullmatch(r"\d{4}", f):
            year = f
            if len(buf) >= 2:
                degree, institution = buf[0], buf[1]
                field = ", ".join(buf[2:])
                head = f"{degree} {field}" if field else degree
                out.append(f"{head}, {institution} ({year})")
            buf = []
        else:
            buf.append(f)
    return out
