#!/usr/bin/env python3
"""Crawl NJIT CS faculty profiles into the knowledge base (contacts).

DRY-RUN BY DEFAULT: fetches profiles politely and PRINTS the records it would
insert, writing nothing. Use --commit to upsert for real, then run
`python v2/scripts/rebuild_index.py` to make them searchable.

People are placed by their TRUE affiliation/role (3.A): the department in their
title maps to an org (Computer Science -> CS, Ying Wu College of Computing ->
YWCC), and the title maps to a role. Anyone the parser cannot confidently
classify (e.g. a bare "Director") is NOT inserted -- it goes to a REVIEW LIST
(printed and written to docs/faculty_review.txt) for you to handle manually.

Source: people.njit.edu/profile/<user> -- NJIT's canonical, server-rendered
profiles -- discovered from cs.njit.edu/faculty. Politeness: self-identifying
User-Agent (project URL, no personal data), rate limiting, single batch.

Usage:
  python scripts/crawl_cs_faculty.py                 # dry run, 3 profiles
  python scripts/crawl_cs_faculty.py --limit 58      # dry run, all
  python scripts/crawl_cs_faculty.py --commit        # write resolved records
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "gsa_gateway.db"
REVIEW_FILE = REPO / "docs" / "faculty_review.txt"
FACULTY_LIST = "https://cs.njit.edu/faculty"
UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"

# department string (lowercased) -> org id
ORG_MAP = {
    "computer science": 5,
    "ying wu college of computing": 4,
    "data science": 6,
    "informatics": 7,
}
# academic ranks (longest/most-specific first) -> vocab.contact_roles
ACADEMIC_RANKS = [
    ("distinguished professor", "professor"),
    ("research professor", "professor"),
    ("teaching professor", "professor"),
    ("associate professor", "associate_professor"),
    ("assistant professor", "assistant_professor"),
    ("professor", "professor"),
    ("university lecturer", "lecturer"),
    ("senior lecturer", "lecturer"),
    ("lecturer", "lecturer"),
]
# administrative titles -> role (used only if no academic rank present)
ADMIN_ROLES = [
    ("associate dean", "associate_dean"),
    ("vice dean", "associate_dean"),
    ("dean", "dean"),
    ("department chair", "chair"),
    ("chair", "chair"),
]
PUB_TYPE = re.compile(r"^(Journal Article|Conference Proceeding|Book Chapter|Book|Thesis|"
                      r"Technical Report|Report|Patent|Presentation|Magazine Article|Other)\s*", re.I)


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def discover() -> list[str]:
    html = fetch(FACULTY_LIST)
    seen, out = set(), []
    for m in re.findall(r"(?:https:)?//people\.njit\.edu/profile/[A-Za-z0-9_-]+", html):
        u = "https:" + m if m.startswith("//") else m
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def classify(title_lines: list[str]):
    """Return (role, admin_titles, org_id, department, review_reason)."""
    role, admin, dept = None, [], None
    for line in title_lines:
        low = line.lower()
        if role is None:
            for phrase, r in ACADEMIC_RANKS:
                if phrase in low:
                    role = r
                    break
        for phrase, r in ADMIN_ROLES:
            if phrase in low and (line, r) not in admin:
                admin.append((line, r))
        for dname, oid in ORG_MAP.items():
            if dname in low:
                dept = dname  # last/most-specific match wins
    if role is None and admin:          # pure-administrative person (e.g. a Dean)
        role = admin[0][1]
    org_id = ORG_MAP.get(dept) if dept else None
    reason = None
    if role is None:
        reason = "unrecognized role"
    elif org_id is None:
        reason = "unmapped department"
    return role, [a[0] for a in admin], org_id, dept, reason


def parse_profile(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    name = title.get_text().split("|")[0].strip() if title else ""

    # title/role lines live in div.position; multi-title people separate them
    # with <br>, so split on that to keep each title distinct.
    title_lines: list[str] = []
    for d in soup.select("div.position"):
        for part in re.split(r"(?i)<br\s*/?>", d.decode_contents()):
            t = re.sub(r"\s+", " ", BeautifulSoup(part, "html.parser").get_text(" ", strip=True)).strip()
            if t and t not in title_lines:
                title_lines.append(t)
    role, admin_titles, org_id, dept, reason = classify(title_lines)

    em = re.search(r"[A-Za-z0-9._%+-]+@njit\.edu", html)
    email = em.group(0) if em else None
    ph = soup.find(class_="phone1")
    phone = ph.get_text(" ", strip=True) if ph else None
    om = re.search(r"\b\d{3,4}\s+[A-Z][A-Za-z .]{5,60}?(?:Center|Hall|Building|GITC|Tower)\b[^<]{0,25}", html)
    office = om.group(0).strip() if om else None

    sections = {}
    tc = soup.find("div", class_="tabbed-content")
    if tc:
        nav = [a.get("data-target") for a in tc.select("a.tab-control")]
        sections = dict(zip(nav, tc.find_all("div", class_="tab-content")))

    def pane(key):
        return sections[key] if key in sections else None

    research = pane("research").get_text(" ", strip=True) if pane("research") else ""
    teaching = pane("teaching").get_text(" ", strip=True) if pane("teaching") else ""
    about_el = pane("about")
    about_txt = about_el.get_text(" ", strip=True) if about_el else ""
    bio = ""
    bm = re.search(r"About Me\s+(.*?)(?:\s+Education\b|$)", about_txt)
    if bm:
        bio = bm.group(1).strip()[:600]

    pubs, seen = [], set()
    if pane("publications"):
        for div in pane("publications").find_all("div"):
            txt = PUB_TYPE.sub("", re.sub(r"\s+", " ", div.get_text(" ", strip=True)).strip())
            key = txt[:100].lower()
            if len(txt) > 40 and re.search(r"\b(19|20)\d{2}\b", txt) and key not in seen:
                seen.add(key)
                pubs.append(txt)
    pubs = pubs[:10]

    about_html = str(about_el) if about_el else ""
    links = {}
    for label, pat in [("scholar", "scholar.google"), ("orcid", "orcid.org"), ("github", "github.com")]:
        lm = re.search(r"https?://[^\"' ]*" + pat + r"[^\"' ]*", about_html)
        if lm:
            links[label] = lm.group(0)
    wm = re.search(r'href="(https?://[^"]+)"[^>]*>\s*(?:Website|Web ?[Pp]age|Homepage)', about_html)
    if wm:
        links["webpage"] = wm.group(1)

    return {
        "name": name, "title_lines": title_lines, "role": role, "admin_titles": admin_titles,
        "department": dept, "org_id": org_id, "review_reason": reason,
        "email": email, "phone": phone, "office": office, "bio": bio,
        "research": research, "teaching": teaching, "publications": pubs, "links": links,
        "source_url": url, "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_record(p: dict) -> dict:
    head = f"{p['name']} -- {'; '.join(p['title_lines'])}".strip(" -")
    parts = [head]
    if p["bio"]:
        parts.append(p["bio"])
    if p["research"]:
        parts.append("Research: " + p["research"][:800])
    if p["publications"]:
        parts.append("Selected publications:\n- " + "\n- ".join(p["publications"]))
    contact = " · ".join(x for x in [p["email"], p["phone"], p["office"]] if x)
    if contact:
        parts.append("Contact: " + contact)
    metadata = {
        "role": p["role"], "name": p["name"], "title": "; ".join(p["title_lines"]),
        "admin_titles": p["admin_titles"], "affiliation": p["department"],
        "email": p["email"], "phone": p["phone"], "office": p["office"],
        "research_areas": p["research"][:500], "publications": p["publications"],
        "source_url": p["source_url"], "crawled_at": p["crawled_at"], **p["links"],
    }
    return {"org_id": p["org_id"], "type": "contact", "title": p["name"],
            "content": "\n\n".join(parts), "metadata": metadata, "source_url": p["source_url"]}


def upsert(conn: sqlite3.Connection, rec: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute("SELECT id, root_id FROM knowledge_items WHERE source_url=? AND is_active=1",
                       (rec["source_url"],)).fetchone()
    if row:
        old_id, root = row[0], (row[1] or row[0])
        ver = conn.execute("SELECT COALESCE(MAX(version),1) FROM knowledge_items WHERE root_id=?",
                           (root,)).fetchone()[0]
        conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=? WHERE id=?", (now, old_id))
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,root_id,parent_id,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (rec["org_id"], rec["type"], rec["title"], rec["content"],
                      json.dumps(rec["metadata"]), rec["source_url"], ver + 1, root, old_id, "crawler"))
        return "updated"
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,created_by) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (rec["org_id"], rec["type"], rec["title"], rec["content"],
                  json.dumps(rec["metadata"]), rec["source_url"], "crawler"))
    return "inserted"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write resolved records (default: dry run)")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    print(f"Discovering faculty from {FACULTY_LIST} ...")
    profiles = discover()
    print(f"  {len(profiles)} profile links; crawling up to {args.limit} (delay {args.delay}s)\n")

    resolved, review = [], []
    for url in profiles[:args.limit]:
        try:
            p = parse_profile(url, fetch(url))
        except Exception as exc:  # noqa: BLE001
            review.append({"name": url, "review_reason": f"fetch/parse error: {exc}",
                           "title_lines": [], "email": None, "source_url": url})
            time.sleep(args.delay)
            continue
        (review if p["review_reason"] else resolved).append(p)
        tag = "REVIEW" if p["review_reason"] else f"org {p['org_id']} / {p['role']}"
        print(f"[{tag}] {p['name'] or '(no name)'}  |  {'; '.join(p['title_lines']) or '(no title)'}")
        if not p["review_reason"]:
            print(f"        email={p['email']} phone={p['phone']} office={p['office']}")
            print(f"        pubs={len(p['publications'])} research={'yes' if p['research'] else 'no'} "
                  f"links={list(p['links'])}")
            if p["admin_titles"]:
                print(f"        also: {p['admin_titles']}")
        else:
            print(f"        reason: {p['review_reason']}")
        time.sleep(args.delay)

    # write the review list for manual handling
    if review:
        REVIEW_FILE.write_text(
            "# Faculty needing manual review (not auto-inserted)\n\n" +
            "\n".join(f"- {r['name']}  [{r['review_reason']}]\n  titles: {'; '.join(r.get('title_lines') or [])}"
                      f"\n  email: {r.get('email')}\n  url: {r['source_url']}\n" for r in review),
            encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  resolved (would insert): {len(resolved)}")
    print(f"  needs manual review    : {len(review)}  -> {REVIEW_FILE if review else '(none)'}")

    if args.commit:
        conn = sqlite3.connect(str(DB))
        results = [upsert(conn, build_record(p)) for p in resolved]
        conn.commit(); conn.close()
        print(f"\nCOMMITTED: {results.count('inserted')} inserted, {results.count('updated')} updated.")
        print("Next: add any new roles to vocab.contact_roles, then "
              "python v2/scripts/rebuild_index.py")
    else:
        print("\nDRY RUN -- nothing written. Review the list, then re-run with --commit.")


if __name__ == "__main__":
    main()
