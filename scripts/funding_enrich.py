#!/usr/bin/env python3
"""Federal research-funding enrichment (NSF + NIH) — add per-faculty funding to the KG.

Repeatable, org-scoped, MULTI-SOURCE, GATED (dry-run default; --commit takes a hardened_backup
first). Data-bringing only (crawl-brings-data hard line): writes additive attrs bags, never
touches usage/serving. Supersedes scripts/nsf_enrich.py (NSF logic unchanged; NIH added).
  - attrs.funding.nsf   : {updated_at, njit_total, matched_by, awards:[{id,title,awardee,
                          start,exp,obligated,at_njit}]}
  - attrs.funding.nih   : {updated_at, njit_total, matched_by, projects:[{core,title,total}]}
  - attrs.email_aliases : [{email, source:"nsf", added:<date>}]  (crawled attrs.email untouched)

Shared matching gate (validated on YWCC 2026-07-10, zero fabricated matches):
  * query by FULL NAME; candidate = surname AND FULL given-name match (no initial fallback)
  * LECTURERS skipped (teaching track, no research grants)
NSF identity/attribution: @njit.edu award-email OR awardeeName=NJIT confirms identity; only
  awardeeName=NJIT counts toward the total (prior-institution awards kept, at_njit=false).
NIH is simpler: the API's org_names=NJIT filter gives identity AND attribution in one query, so a
  surname+full-given match on contact_pi_name is sufficient; funding summed per core project.

Usage:
  python scripts/funding_enrich.py --org ywcc                      # dry-run, both sources
  python scripts/funding_enrich.py --org ywcc --source nsf         # one source
  python scripts/funding_enrich.py --org ywcc --only wei --commit  # targeted live write
"""
import argparse, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts._area_tag_migrate import hardened_backup

UA = "GSA-Gateway/1.0 (+https://gsanjit.com; NJIT Graduate Student Association)"
NSF_API = "https://api.nsf.gov/services/v1/awards.json"
NSF_FIELDS = "id,title,fundsObligatedAmt,startDate,expDate,awardeeName,piFirstName,piLastName,piEmail"
NIH_API = "https://api.reporter.nih.gov/v2/projects/search"
NIH_ORG = "NEW JERSEY INSTITUTE OF TECHNOLOGY"


def _today():
    return time.strftime("%Y-%m-%d")


def _amt(x):
    try:
        return int(float(x or 0))
    except (TypeError, ValueError):
        return 0


# ---------- name helpers (handle 'Surname, Given' and 'Given Surname') ----------
def _norm(s):
    return [t for t in re.sub(r"[^a-z ]", " ", (s or "").lower()).split() if len(t) > 1]


def _split(name):
    if "," in name:
        sur, _, giv = name.partition(",")
        s, g = _norm(sur), _norm(giv)
        return (s[-1] if s else None), (g[0] if g else None)
    p = _norm(name)
    return (p[-1] if p else None), (p[0] if len(p) > 1 else None)


def _fullname(name):
    if "," in name:
        sur, _, giv = name.partition(",")
        return " ".join(_norm(giv) + _norm(sur))
    return " ".join(_norm(name))


def _field_match(last, first, surname, giv):
    """Field-AWARE: surname must be in the last-name field, given in the first-name field.
    (Pooling the two fields lets 'Dong Zhi-Wei' match faculty 'Zhi Wei' — a homonym leak.)"""
    return surname in _norm(last) and giv in _norm(first)


def _contact_match(contact_pi_name, surname, giv):
    """NIH contact_pi_name is 'SURNAME, GIVEN M' — split on the comma and match field-aware."""
    if "," in (contact_pi_name or ""):
        last, _, first = contact_pi_name.partition(",")
        return _field_match(last, first, surname, giv)
    toks = _norm(contact_pi_name)
    return surname in toks and giv in toks


# ================= NSF provider =================
def _http(url=None, data=None):
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": UA, **({"Content-Type": "application/json"} if data else {})})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(1.5)
    return None


def _is_njit_awardee(awardee):
    return "jersey institute" in (awardee or "").lower()


def nsf_match(name, our_email, rpp):
    surname, giv = _split(name)
    if not surname or not giv:
        return {"status": "unmatchable"}
    d = _http(f"{NSF_API}?pdPIName={urllib.parse.quote(_fullname(name))}"
              f"&printFields={NSF_FIELDS}&rpp={rpp}")
    if d is None:
        return {"status": "fetch-failed"}
    raw = d.get("response", {}).get("award", []) or []
    truncated = len(raw) >= rpp
    cands = [a for a in raw if _field_match(a.get("piLastName"), a.get("piFirstName"), surname, giv)]
    if not cands:
        return {"status": "no-award", "truncated": truncated}
    njit_emails = {(a.get("piEmail") or "").lower() for a in cands
                   if (a.get("piEmail") or "").lower().endswith("@njit.edu")}
    njit_awards = [a for a in cands if _is_njit_awardee(a.get("awardeeName"))]
    if not njit_emails and not njit_awards:
        return {"status": "review", "n_cands": len(cands), "reason": "no-njit-evidence"}
    if len(njit_emails) > 1:
        return {"status": "review", "n_cands": len(cands), "reason": "multiple-njit-emails"}
    exact = bool(our_email and our_email.lower() in njit_emails)
    basis = ("exact-email" if exact else "name+njit-email" if njit_emails else "name+njit-awardee")

    def row(a):
        return {"id": a.get("id"), "title": a.get("title"), "awardee": a.get("awardeeName"),
                "start": a.get("startDate"), "exp": a.get("expDate"),
                "obligated": _amt(a.get("fundsObligatedAmt")),
                "at_njit": _is_njit_awardee(a.get("awardeeName"))}

    njit_total = sum(_amt(a.get("fundsObligatedAmt")) for a in njit_awards)
    return {"status": "ok", "source": "nsf", "basis": basis, "truncated": truncated,
            "njit_total": njit_total, "n_items": len(njit_awards),
            "bag": {"updated_at": _today(), "njit_total": njit_total, "matched_by": basis,
                    "awards": [row(a) for a in njit_awards] + [row(a) for a in cands
                               if not _is_njit_awardee(a.get("awardeeName"))]},
            "harvested_email": sorted(njit_emails)[0] if njit_emails else None}


# ================= NIH provider =================
def nih_match(name, _our_email, _rpp):
    surname, giv = _split(name)
    if not surname or not giv:
        return {"status": "unmatchable"}
    body = json.dumps({
        "criteria": {"org_names": [NIH_ORG], "pi_names": [{"any_name": _fullname(name)}],
                     "exclude_subprojects": True},          # S3: no P01/U54 parent+child double-count
        "include_fields": ["CoreProjectNum", "ProjectTitle", "PrincipalInvestigators",
                           "AwardAmount", "FiscalYear", "ApplId"],
        "limit": 100}).encode()
    d = _http(NIH_API, data=body)
    if d is None:
        return {"status": "fetch-failed"}
    raw = d.get("results", []) or []
    truncated = d.get("meta", {}).get("total", 0) > len(raw)   # S2
    # For each project, find PI entries matching this person (field-aware), keep role + profile.
    cores = {}                                                  # core -> {total, title, role, fys, pids}
    for p in raw:
        pis = p.get("principal_investigators") or []
        mine = [pi for pi in pis
                if _field_match(pi.get("last_name"), pi.get("first_name"), surname, giv)]
        if not mine:
            continue
        is_contact = any(pi.get("is_contact_pi") for pi in mine)
        core = p.get("core_project_num") or "?"
        c = cores.setdefault(core, {"total": 0, "title": p.get("project_title"),
                                    "role": "contact" if is_contact else "co_pi",
                                    "fys": set(), "pids": set(),
                                    "appl_id": None, "appl_fy": -1})
        c["total"] += _amt(p.get("award_amount"))
        if p.get("fiscal_year"):
            fy = int(p["fiscal_year"])
            c["fys"].add(fy)
            if fy > c["appl_fy"] and p.get("appl_id"):
                c["appl_fy"] = fy
                c["appl_id"] = p["appl_id"]
        for pi in mine:
            if pi.get("profile_id"):
                c["pids"].add(pi["profile_id"])
        if is_contact:                                # a project is "contact" if any year-row is
            c["role"] = "contact"
    if not cores:
        return {"status": "no-award", "truncated": truncated}
    # N2: >1 distinct NIH profile_id among matched rows == two same-named people -> review
    all_pids = set().union(*(c["pids"] for c in cores.values()))
    if len(all_pids) > 1:
        return {"status": "review", "n_cands": len(all_pids), "reason": "multiple-nih-profiles"}
    # njit_total counts CONTACT-PI projects only (co-PI listed but excluded, no cross-faculty double-count)
    njit_total = sum(c["total"] for c in cores.values() if c["role"] == "contact")
    projects = [{"core": k, "title": c["title"], "total": c["total"], "role": c["role"],
                 "fy_first": min(c["fys"]) if c["fys"] else None,
                 "fy_last": max(c["fys"]) if c["fys"] else None,
                 "appl_id": c["appl_id"]}
                for k, c in cores.items()]
    n_contact = sum(1 for c in cores.values() if c["role"] == "contact")
    return {"status": "ok", "source": "nih", "basis": "org+name", "truncated": truncated,
            "njit_total": njit_total, "n_items": n_contact,
            "bag": {"updated_at": _today(), "njit_total": njit_total, "matched_by": "org+name",
                    "projects": projects}}


PROVIDERS = {"nsf": nsf_match, "nih": nih_match}


# ---------- KG helpers ----------
def _org_and_children(c, slug):
    row = c.execute(
        "SELECT n.id FROM nodes n JOIN organizations o "
        "ON o.id=json_extract(n.attrs,'$.org_id') WHERE n.type='Org' AND o.slug=? LIMIT 1",
        (slug,)).fetchone()
    if not row:
        sys.exit(f"org '{slug}' not found")
    root = row[0]
    kids = [r[0] for r in c.execute(
        "SELECT n.id FROM nodes n JOIN edges e ON e.src_id=n.id "
        "WHERE e.type='part_of' AND e.dst_id=? AND e.is_active=1 AND n.type='Org'", (root,))]
    return [root] + kids


def _is_lecturer(c, node_id):
    titles = []
    for (a,) in c.execute("SELECT attrs FROM edges WHERE src_id=? AND type='has_role' "
                          "AND category='faculty' AND is_active=1", (node_id,)):
        titles += (json.loads(a or "{}").get("titles") or [])
    return bool(titles) and any("lecturer" in t.lower() for t in titles)


def research_faculty(c, org_ids):
    seen, out = set(), []
    for oid in org_ids:
        for r in c.execute(
            "SELECT DISTINCT n.id,n.name,n.attrs FROM nodes n JOIN edges e ON e.src_id=n.id "
            "WHERE n.type='Person' AND n.is_active=1 AND e.type='has_role' "
            "AND e.category='faculty' AND e.dst_id=? AND e.is_active=1 ORDER BY n.name", (oid,)):
            if r[0] in seen or _is_lecturer(c, r[0]):
                continue
            seen.add(r[0])
            out.append(r)
    return out


def add_alias(attrs, name, email):
    """Add a harvested NSF email as an alias if new AND its local-part shares a name token."""
    if not email:
        return None
    prim = (attrs.get("email") or "").lower()
    aliases = attrs.setdefault("email_aliases", [])
    have = {prim} | {x.get("email", "").lower() for x in aliases}
    local = email.split("@")[0]
    toks = set(_norm(name))
    shares = any(t in local or local in t for t in toks) if toks else False
    if email not in have and shares:
        aliases.append({"email": email, "source": "nsf", "added": _today()})
        return email
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="ywcc")
    ap.add_argument("--source", default="nsf,nih", help="comma list: nsf,nih")
    ap.add_argument("--only", default=None, help="case-insensitive name substring filter")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--rpp", type=int, default=25)
    ap.add_argument("--pace", type=float, default=0.4)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    sources = [s.strip() for s in args.source.split(",") if s.strip() in PROVIDERS]
    if not sources:
        sys.exit("no valid --source (nsf,nih)")

    live = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "gsa_gateway.db")
    if args.commit and os.path.abspath(args.db) == live:
        hardened_backup(args.db, label="funding_enrich")
        print("hardened_backup taken.")

    c = sqlite3.connect(args.db)
    fac = research_faculty(c, _org_and_children(c, args.org))
    if args.only:
        fac = [f for f in fac if args.only.lower() in f[1].lower()]
    print(f"{args.org}: {len(fac)} research faculty | sources={sources}"
          f"{' | --only' if args.only else ''}\n")

    agg = {s: {"funded": 0, "items": 0, "dollars": 0} for s in sources}
    n_alias = 0
    review, failed, unmatch, truncated, kept = [], [], [], [], []
    to_write = {}                                    # pid -> new_attrs (merged across sources)
    for pid, name, raw_attrs in fac:
        attrs = json.loads(raw_attrs or "{}")
        our_email = attrs.get("email")
        touched = False
        line = []
        for s in sources:
            m = PROVIDERS[s](name, our_email, args.rpp)
            time.sleep(args.pace)
            st = m["status"]
            # S5: a non-ok status never erases a previously written bag — surface the retention.
            if st != "ok" and (attrs.get("funding") or {}).get(s):
                kept.append(f"{name}/{s} (source now '{st}', existing bag kept)")
            if st == "fetch-failed":
                failed.append(f"{name}/{s}"); continue
            if st == "unmatchable":
                if name not in unmatch:
                    unmatch.append(name)
                continue
            if m.get("truncated") and name not in truncated:
                truncated.append(name)
            if st in ("no-award",):
                continue
            if st == "review":
                review.append(f"{name}/{s} ({m['n_cands']} cands, {m.get('reason')})")
                continue
            # ok
            attrs.setdefault("funding", {})[s] = m["bag"]
            agg[s]["funded"] += 1 if m["n_items"] else 0
            agg[s]["items"] += m["n_items"]; agg[s]["dollars"] += m["njit_total"]
            touched = True
            line.append(f"{s.upper()} {m['n_items']}×${m['njit_total']:,}[{m['basis']}]")
            if s == "nsf":
                al = add_alias(attrs, name, m.get("harvested_email"))
                if al:
                    n_alias += 1; line.append(f"+alias:{al}")
        if touched:
            print(f"  {name:28} " + "  ".join(line))
            to_write[pid] = attrs

    print("\n--- summary ---")
    for s in sources:
        a = agg[s]
        print(f"  {s.upper()}: funded={a['funded']} items={a['items']} ${a['dollars']:,}")
    print(f"  aliases_added={n_alias}  nodes_to_write={len(to_write)}  "
          f"review={len(review)}  unmatchable={len(unmatch)}  fetch_failed={len(failed)}  "
          f"truncated={len(truncated)}")
    for r in review:
        print("   review:", r)
    if truncated:
        print("   ⚠ TRUNCATED (rpp cap, may undercount):", ", ".join(truncated))
    if unmatch:
        print("   unmatchable (no usable given name):", ", ".join(unmatch))
    if failed:
        print("   ⚠ FETCH-FAILED (API error, NOT 'no funding'):", ", ".join(failed))
    if kept:
        print("   ℹ kept existing bags (source now non-ok):", "; ".join(kept))

    if args.commit:
        for pid, new_attrs in to_write.items():
            c.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                      (json.dumps(new_attrs), pid))
        c.commit()
        print(f"\nCOMMITTED {len(to_write)} node updates to {args.db}")
    else:
        print(f"\nDRY RUN — {len(to_write)} nodes would change. Re-run with --commit.")
    c.close()


if __name__ == "__main__":
    main()
