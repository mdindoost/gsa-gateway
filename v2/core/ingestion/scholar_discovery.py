"""Scholar URL discovery — find + add a Google Scholar URL for a faculty member who lacks one,
WITHOUT ever attaching the wrong person's profile.

`classify_candidate` is the SOLE anti-fabrication boundary for this feature (metrics/areas render
deterministically, with no answer-time LLM guard). A STRICT (auto-write) match requires a verified
``njit.edu`` email AND a strong name match AND either a unique surname among active NJIT people OR a
corroborating signal (department/affiliation match, or Scholar-interest ∩ existing research-area).
Everything else is queued for human review and never written. Spec:
docs/superpowers/specs/2026-06-20-scholar-url-discovery-design.md
"""
from __future__ import annotations

import datetime
import json
import random
import re
import time
import unicodedata

from bs4 import BeautifulSoup

_SCHOLAR_RE = re.compile(r"scholar\.google\.[a-z.]+/citations", re.I)


# ── profile parsing ───────────────────────────────────────────────────────────
def _verified_domain(text: str) -> str | None:
    m = re.search(r"verified email at\s+([A-Za-z0-9.\-]+)", text or "", re.I)
    return m.group(1).lower().rstrip(".") if m else None


def parse_profile_identity(html: str) -> dict:
    """{name, verified_email_domain|None, affiliation, blocked}. ``blocked`` is True when the page
    has no profile name element (captcha/robot wall) — distinct from 'no verified email'."""
    soup = BeautifulSoup(html or "", "html.parser")
    name_el = soup.select_one("#gsc_prf_in")
    if name_el is None:
        return {"name": None, "verified_email_domain": None, "affiliation": "", "blocked": True}
    ivh = soup.select_one("#gsc_prf_ivh")
    aff = soup.select_one("#gsc_prf_ila") or soup.select_one(".gsc_prf_il")
    return {
        "name": name_el.get_text(strip=True),
        "verified_email_domain": _verified_domain(ivh.get_text(" ", strip=True) if ivh else ""),
        "affiliation": aff.get_text(" ", strip=True) if aff else "",
        "blocked": False,
    }


# ── name matching ───────────────────────────────────────────────────────────--
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))   # strip accents
    return s.casefold().strip()


def _parts(name: str) -> tuple[str, str, str]:
    """(first, middle_initial, last) from 'First [M.] Last' or 'Last, First [M.]'."""
    n = _norm(name)
    if "," in n:
        last, _, rest = n.partition(",")
        toks = rest.split() + last.split()
        # 'Last, First M' -> first=rest[0], last=last
        first = rest.split()[0] if rest.split() else ""
        last = last.strip()
    else:
        toks = n.split()
        first = toks[0] if toks else ""
        last = toks[-1] if toks else ""
    mid = ""
    body = n.replace(",", " ").split()
    # middle initial = a single-letter token that isn't first/last position
    for t in body[1:-1]:
        t2 = t.rstrip(".")
        if len(t2) == 1:
            mid = t2
    return first.rstrip("."), mid, last


def _is_initial(tok: str) -> bool:
    return len(tok.rstrip(".")) == 1


def name_matches(kg_name: str, profile_name: str) -> bool:
    """STRICT: same surname, same FULL first name (not a bare initial), and no CONFLICTING middle
    initial. A middle initial present on only one side is neutral."""
    f1, m1, l1 = _parts(kg_name)
    f2, m2, l2 = _parts(profile_name)
    if not l1 or l1 != l2:
        return False
    if not f1 or not f2 or _is_initial(f1) or _is_initial(f2):
        return False                       # need full first names on both sides for strict
    if f1 != f2:
        return False
    if m1 and m2 and m1 != m2:             # conflicting middle initials
        return False
    return True


def name_plausible(kg_name: str, profile_name: str) -> bool:
    """Looser: same surname AND first names share an initial (routes near-misses — e.g. a bare
    first initial — to the review queue; NEVER used for a strict auto-write)."""
    f1, _, l1 = _parts(kg_name)
    f2, _, l2 = _parts(profile_name)
    if not l1 or l1 != l2 or not f1 or not f2:
        return False
    return f1[0] == f2[0]


# ── disambiguation (DB-backed) ────────────────────────────────────────────────
def surname_is_unique(conn, kg_name: str) -> bool:
    """True iff exactly one active person carries this surname (the safe auto-write case)."""
    target = _parts(kg_name)[2]
    if not target:
        return False
    n = 0
    for (raw,) in conn.execute(
            "SELECT name FROM nodes WHERE type='Person' AND is_active=1").fetchall():
        if raw and _parts(raw)[2] == target:
            n += 1
            if n > 1:
                return False
    return n == 1


def _home_org_name(conn, person_key: str) -> str | None:
    row = conn.execute(
        "SELECT o.name FROM edges e JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id AND o.type='Org' AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.key=? ORDER BY e.id LIMIT 1",
        (person_key,)).fetchone()
    return row[0] if row else None


def _person_areas(conn, person_key: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT a.name FROM edges e JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes a ON a.id=e.dst_id AND a.type='ResearchArea' AND a.is_active=1 "
        "WHERE e.type='researches' AND e.is_active=1 AND p.key=?", (person_key,)).fetchall()]


def corroborates(conn, person_key: str, identity: dict, interests: list[str]) -> str | None:
    """A second signal that this profile is THIS person (for surname-collision cases):
    department/affiliation match, or Scholar-interest ∩ existing research-area. None if neither."""
    aff = _norm(identity.get("affiliation") or "")
    org = _home_org_name(conn, person_key)
    if org and len(_norm(org)) >= 4 and _norm(org) in aff:
        return "dept_match"
    areas = {_norm(a) for a in _person_areas(conn, person_key)}
    if areas and any(_norm(i) in areas for i in (interests or [])):
        return "interest_overlap"
    return None


def mark_attempted(conn, person_key: str, decision: str, today: str) -> None:
    """Record a non-strict discovery outcome so the person drops out of future target sets (the B1
    termination fix). Writes ``attrs.profiles.scholar.discovery_attempted = {date, decision}`` (NO
    url) via the deep-merging set_person_profiles. Does NOT commit. NOT used for 'blocked' (transient
    throttle — we want to retry those) or 'strict' (that writes a url)."""
    from v2.core.ingestion.people_editor import set_person_profiles
    set_person_profiles(conn, person_key=person_key,
                        profiles={"scholar": {"discovery_attempted": {"date": today, "decision": decision}}})


def _attempted_keys(conn, retry_after_days: int | None, today: datetime.date | None) -> set[str]:
    from v2.core.ingestion.scholar import _parse_updated
    today = today or datetime.date.today()
    out: set[str] = set()
    for key, raw in conn.execute(
            "SELECT key, attrs FROM nodes WHERE type='Person' AND is_active=1 "
            "AND attrs LIKE '%discovery_attempted%'").fetchall():
        try:
            da = ((json.loads(raw or "{}").get("profiles") or {}).get("scholar") or {}).get("discovery_attempted")
        except (TypeError, ValueError):
            continue
        if not da:
            continue
        if retry_after_days is not None:                       # stale attempts re-open for retry
            d = _parse_updated(da.get("date"))
            if d and (today - d).days >= retry_after_days:
                continue
        out.add(key)
    return out


def select_discovery_targets(conn, *, org_scope: str | None = None, limit: int | None = None,
                             skip_attempted: bool = True, retry_after_days: int | None = None,
                             today: datetime.date | None = None) -> list[tuple[str, str]]:
    """(key, name) for active FACULTY in scope who DON'T yet have a Scholar URL — discovery's set.
    Faculty-only (`has_role.category='faculty'`), org subtree (college includes its departments),
    distinct, capped at ``limit``. ``skip_attempted`` (default) also excludes anyone already tried
    (a `discovery_attempted` marker) — unless their attempt is older than ``retry_after_days``. This
    is what makes the sweep terminate + truly resume. Read-only."""
    from v2.core.ingestion.scholar import people_with_scholar
    have_url = {k for k, _ in people_with_scholar(conn)}
    if skip_attempted:
        have_url |= _attempted_keys(conn, retry_after_days, today)
    base = ("SELECT DISTINCT p.key, p.name FROM edges e JOIN nodes p ON p.id=e.src_id "
            "{join} WHERE e.type='has_role' AND e.is_active=1 AND e.category='faculty' "
            "AND p.is_active=1 {where} ORDER BY p.name")
    if org_scope is not None:
        from v2.core.retrieval.skills import org_descendants
        row = conn.execute(
            "SELECT id FROM organizations WHERE slug=? AND is_active=1", (org_scope,)).fetchone()
        if not row:
            return []
        ids = sorted(org_descendants(conn, row[0]))
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        sql = base.format(join="JOIN nodes o ON o.id=e.dst_id AND o.is_active=1",
                          where=f"AND json_extract(o.attrs,'$.org_id') IN ({ph})")
        rows = conn.execute(sql, tuple(ids)).fetchall()
    else:
        rows = conn.execute(base.format(join="", where="")).fetchall()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for k, name in rows:
        if k in have_url or k in seen:
            continue
        seen.add(k)
        out.append((k, name))
    return out[:limit] if limit is not None else out


def classify_candidate(conn, person_key: str, kg_name: str, identity: dict,
                       interests: list[str]) -> dict:
    """{decision, basis}. decision ∈ strict|uncertain|blocked|reject. STRICT only behind the
    verified-njit + name-match + (unique-surname OR corroboration) gate — the anti-fabrication
    boundary (no answer-time guard downstream)."""
    if identity.get("blocked"):
        return {"decision": "blocked", "basis": None}
    dom = identity.get("verified_email_domain")
    pname = identity.get("name") or ""
    if not name_matches(kg_name, pname):
        if dom == "njit.edu" and name_plausible(kg_name, pname):
            return {"decision": "uncertain", "basis": None}     # e.g. bare-initial first name
        return {"decision": "reject", "basis": None}
    if dom and dom != "njit.edu":
        return {"decision": "reject", "basis": None}            # verified at another school
    if dom is None:
        return {"decision": "uncertain", "basis": None}         # NJIT only in free text
    # verified njit.edu + strong name match:
    if surname_is_unique(conn, kg_name):
        return {"decision": "strict", "basis": "unique_surname"}
    basis = corroborates(conn, person_key, identity, interests)
    if basis:
        return {"decision": "strict", "basis": basis}
    return {"decision": "uncertain", "basis": None}             # homonym, no corroboration


# ── orchestration ───────────────────────────────────────────────────────────--
def _query(name: str) -> str:
    return f'"{name}" NJIT Google Scholar'


def discover_for_person(conn, person, *, web_search, fetch, max_candidates: int = 5) -> dict:
    """Search + classify one person. {decision, url, reason, html, interests, basis}. A single
    strict candidate wins; >=2 strict -> uncertain (ambiguous, never auto-pick); else best
    uncertain; else blocked (all fetches captcha'd) / skip (no match)."""
    from v2.core.ingestion.scholar import parse_scholar_interests
    key, name = person
    urls = [u for u in (web_search(_query(name), k=max_candidates) or []) if _SCHOLAR_RE.search(u)]
    strict: list[dict] = []
    uncertain: dict | None = None
    any_blocked = False
    for u in urls[:max_candidates]:
        try:
            html, status = fetch(u)
        except Exception:
            continue
        if not html or status != "ok":
            continue
        ident = parse_profile_identity(html)
        if ident["blocked"]:
            any_blocked = True
            continue
        interests = parse_scholar_interests(html)
        c = classify_candidate(conn, key, name, ident, interests)
        if c["decision"] == "strict":
            strict.append({"url": u, "html": html, "interests": interests, "basis": c["basis"]})
        elif c["decision"] == "uncertain" and uncertain is None:
            uncertain = {"url": u, "reason": "uncertain match — needs review"}
    if len(strict) == 1:
        return {"decision": "strict", "reason": strict[0]["basis"], **strict[0]}
    if len(strict) >= 2:
        return {"decision": "uncertain", "url": strict[0]["url"], "reason": "multiple strict candidates"}
    if uncertain:
        return {"decision": "uncertain", **uncertain}
    if any_blocked:
        return {"decision": "blocked", "url": None, "reason": "scholar blocked the fetch"}
    return {"decision": "skip", "url": None, "reason": "no matching profile found"}


def _write_discovered(conn, key: str, res: dict, today: str) -> None:
    from v2.core.ingestion.scholar import parse_scholar_metrics, _home_org_id
    from v2.core.ingestion.people_editor import set_person_profiles, set_person_research_areas
    metrics = parse_scholar_metrics(res["html"]) or {}
    bag = {"url": res["url"], **metrics, "updated_at": today,
           "discovered_by": "auto", "discovered_at": today, "match_basis": res.get("basis")}
    set_person_profiles(conn, person_key=key, profiles={"scholar": bag})
    interests = res.get("interests") or []
    org_id = _home_org_id(conn, key) if interests else None
    if interests and org_id is not None:
        set_person_research_areas(conn, person_key=key, areas=interests, org_id=org_id)


def run(conn, *, web_search, fetch, org_scope: str | None = None, limit: int | None = None,
        delay: float = 3.0, today: str | None = None, max_brave: int = 10 ** 9,
        block_abort: int = 5, max_candidates: int = 5) -> dict:
    """Discover + (strict) write Scholar URLs for faculty-without-Scholar in scope. Writes only
    strict matches (provenance-tagged); queues uncertain; aborts after ``block_abort`` consecutive
    Scholar blocks; stops at the ``max_brave`` search cap. Does NOT commit (caller owns the txn)."""
    today = today or datetime.date.today().strftime("%Y-%m-%d")
    targets = select_discovery_targets(conn, org_scope=org_scope, limit=limit)
    stats = {"scanned": 0, "written": 0, "queued": 0, "skipped": 0, "blocked": 0,
             "brave_calls": 0, "queue": [], "errors": []}
    consecutive = 0
    for i, (key, name) in enumerate(targets):
        if stats["brave_calls"] >= max_brave:
            break
        if i and delay:
            time.sleep(delay)
        stats["scanned"] += 1
        stats["brave_calls"] += 1
        try:
            res = discover_for_person(conn, (key, name), web_search=web_search,
                                      fetch=fetch, max_candidates=max_candidates)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append((key, str(exc))); stats["skipped"] += 1; consecutive = 0
            continue
        d = res["decision"]
        if d == "strict":
            _write_discovered(conn, key, res, today); stats["written"] += 1; consecutive = 0
        elif d == "uncertain":
            stats["queued"] += 1
            stats["queue"].append((key, name, res.get("url"), res.get("reason")))
            mark_attempted(conn, key, "uncertain", today); consecutive = 0     # drops out of future runs
        elif d == "blocked":
            stats["blocked"] += 1; consecutive += 1                            # NOT marked — retry later
            if consecutive >= block_abort:
                break
        else:
            stats["skipped"] += 1
            mark_attempted(conn, key, "skip", today); consecutive = 0          # dead end — don't re-search
    return stats


def sweep(conn, *, web_search, fetch, sleep, org_scope: str | None = None, chunk: int = 50,
          brave_budget: int, today: str | None = None, jitter: tuple[int, int] = (45, 100),
          block_chunk_limit: int = 5, max_blocked_chunks: int = 3, backoff_seconds: int = 3 * 3600,
          retry_after_days: int | None = None, max_candidates: int = 5,
          should_stop=lambda: False, on_progress=None) -> dict:
    """Long-running slow-drip discovery over faculty-without-Scholar. Reuses discover_for_person +
    _write_discovered + mark_attempted (no classifier change). Terminates via the attempted marker;
    stops at the ``brave_budget`` ceiling, gives up after ``max_blocked_chunks`` Scholar-blocked
    chunks, and exits promptly when ``should_stop`` flips (SIGTERM). Commits per person (ms-long txns).
    Injected ``web_search``/``fetch``/``sleep`` → testable with no network/waits. Caller owns embedding
    + the final non-commit semantics here (we DO commit incrementally since it's a long unattended run)."""
    today = today or datetime.date.today().strftime("%Y-%m-%d")
    today_d = datetime.date.today()
    stats = {"scanned": 0, "written": 0, "queued": 0, "skipped": 0, "blocked": 0,
             "brave_calls": 0, "queue": [], "errors": [], "stopped_reason": "done"}
    blocked_streak = 0
    while not should_stop():
        targets = select_discovery_targets(conn, org_scope=org_scope, limit=chunk,
                                           retry_after_days=retry_after_days, today=today_d)
        if not targets:
            stats["stopped_reason"] = "done"; break
        chunk_blocked = 0
        for key, name in targets:
            if should_stop():
                stats["stopped_reason"] = "interrupted"; return stats
            if stats["brave_calls"] >= brave_budget:
                stats["stopped_reason"] = "budget"; return stats
            stats["brave_calls"] += 1                          # before the call (count even on exception)
            try:
                res = discover_for_person(conn, (key, name), web_search=web_search,
                                          fetch=fetch, max_candidates=max_candidates)
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append((key, str(exc))); stats["skipped"] += 1
                conn.commit(); sleep(random.uniform(*jitter)); continue
            d = res["decision"]; stats["scanned"] += 1
            if d == "strict":
                _write_discovered(conn, key, res, today); stats["written"] += 1
            elif d == "uncertain":
                stats["queued"] += 1
                stats["queue"].append((key, name, res.get("url"), res.get("reason")))
                mark_attempted(conn, key, "uncertain", today)
            elif d == "blocked":
                stats["blocked"] += 1; chunk_blocked += 1      # NOT marked — retry later
            else:
                stats["skipped"] += 1; mark_attempted(conn, key, "skip", today)
            conn.commit()
            if on_progress:
                on_progress(stats, key, name, d)
            if chunk_blocked >= block_chunk_limit:             # stop wasting the chunk once throttled
                break
            sleep(random.uniform(*jitter))                     # the slow drip
        if chunk_blocked >= block_chunk_limit:
            blocked_streak += 1
            if blocked_streak >= max_blocked_chunks:
                stats["stopped_reason"] = "blocked"; return stats
            sleep(backoff_seconds)                             # pause and resume (interruptible sleep)
        else:
            blocked_streak = 0
    return stats
