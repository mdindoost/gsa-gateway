#!/usr/bin/env python3
"""Crawl NJIT CS faculty profiles into the knowledge base (contacts under CS).

DRY-RUN BY DEFAULT: fetches profiles politely and PRINTS the records it would
insert, writing nothing to the database. Use --commit to upsert for real, then
run `python v2/scripts/rebuild_index.py` to make them searchable.

Source: people.njit.edu/profile/<user> -- NJIT's canonical, server-rendered
faculty profiles -- discovered from the CS faculty listing. Politeness: a
self-identifying User-Agent (project URL, no personal data), rate limiting, and
a single sequential batch. robots.txt permits these paths.

Usage:
  python scripts/crawl_cs_faculty.py                 # dry run, 3 profiles
  python scripts/crawl_cs_faculty.py --limit 5       # dry run, 5 profiles
  python scripts/crawl_cs_faculty.py --commit        # write to DB (idempotent)
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
CS_ORG_ID = 5
FACULTY_LIST = "https://cs.njit.edu/faculty"
UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"

# title phrase (lowercased) -> vocab.contact_roles value (longest match wins)
ROLE_MAP = [
    ("distinguished professor", "professor"),
    ("associate professor", "associate_professor"),
    ("assistant professor", "assistant_professor"),
    ("university lecturer", "lecturer"),
    ("senior lecturer", "lecturer"),
    ("lecturer", "lecturer"),
    ("director", "lab_director"),
    ("professor", "professor"),
]


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def discover() -> list[str]:
    html = fetch(FACULTY_LIST)
    seen, out = set(), []
    # links on the listing are protocol-relative: href="//people.njit.edu/profile/<user>"
    for m in re.findall(r"(?:https:)?//people\.njit\.edu/profile/[A-Za-z0-9_-]+", html):
        u = "https:" + m if m.startswith("//") else m
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_role(title_line: str) -> str:
    t = title_line.lower()
    for phrase, role in ROLE_MAP:
        if phrase in t:
            return role
    return "professor"


def parse_profile(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    name = title.get_text().split("|")[0].strip() if title else ""

    m = re.search(r"((?:Distinguished |Associate |Assistant )?Professor|"
                  r"(?:Senior |University )?Lecturer)\s*,\s*([A-Z][A-Za-z& ]+)", html)
    title_line = m.group(0).strip() if m else ""
    department = m.group(2).strip() if m else ""
    role = parse_role(title_line)

    em = re.search(r"[A-Za-z0-9._%+-]+@njit\.edu", html)
    email = em.group(0) if em else None

    sections = {}
    tc = soup.find("div", class_="tabbed-content")
    if tc:
        nav = [a.get("data-target") for a in tc.select("a.tab-control")]
        panes = tc.find_all("div", class_="tab-content")
        sections = dict(zip(nav, panes))

    def pane_text(key):
        return sections[key].get_text(" ", strip=True) if key in sections else ""

    research = pane_text("research")
    teaching = pane_text("teaching")

    # each publication appears as a wrapper div ("Journal Article <cite>") and an
    # inner citation div ("<cite>"); strip the leading type label so they dedupe.
    type_label = re.compile(r"^(Journal Article|Conference Proceeding|Book Chapter|Book|"
                            r"Thesis|Technical Report|Report|Patent|Presentation|"
                            r"Magazine Article|Other)\s*", re.I)
    pubs, seen = [], set()
    if "publications" in sections:
        for div in sections["publications"].find_all("div"):
            txt = re.sub(r"\s+", " ", div.get_text(" ", strip=True)).strip()
            txt = type_label.sub("", txt)
            key = txt[:100].lower()
            if len(txt) > 40 and re.search(r"\b(19|20)\d{2}\b", txt) and key not in seen:
                seen.add(key)
                pubs.append(txt)
    pubs = pubs[:10]

    # links only from the profile's own "about" pane (avoids site-chrome footer
    # links like the NJIT LinkedIn school page); never the page-wide HTML.
    about_html = str(sections["about"]) if "about" in sections else ""
    links = {}
    for label, pat in [("scholar", "scholar.google"), ("orcid", "orcid.org"),
                       ("github", "github.com")]:
        lm = re.search(r"https?://[^\"' ]*" + pat + r"[^\"' ]*", about_html)
        if lm:
            links[label] = lm.group(0)
    wm = re.search(r'href="(https?://[^"]+)"[^>]*>\s*(?:Website|Web ?[Pp]age|Homepage)', about_html)
    if wm:
        links["webpage"] = wm.group(1)

    return {
        "name": name, "role": role, "department": department, "title_line": title_line,
        "email": email, "research": research, "teaching": teaching, "publications": pubs,
        "links": links, "source_url": url,
        "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def build_record(p: dict) -> dict:
    parts = [f"{p['name']} -- {p['title_line']}".strip(" -")]
    if p["research"]:
        parts.append("Research: " + p["research"][:800])
    if p["publications"]:
        parts.append("Selected publications:\n- " + "\n- ".join(p["publications"]))
    if p["email"]:
        parts.append("Contact: " + p["email"])
    content = "\n\n".join(parts)
    metadata = {
        "role": p["role"], "name": p["name"], "title": p["title_line"],
        "affiliation": p["department"], "email": p["email"],
        "research_areas": p["research"][:500], "publications": p["publications"],
        "source_url": p["source_url"], "crawled_at": p["crawled_at"], **p["links"],
    }
    return {"org_id": CS_ORG_ID, "type": "contact", "title": p["name"],
            "content": content, "metadata": metadata, "source_url": p["source_url"]}


def upsert(conn: sqlite3.Connection, rec: dict) -> str:
    """Idempotent by source_url: re-crawls append a new version, never duplicate."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT id, root_id FROM knowledge_items WHERE source_url=? AND is_active=1",
        (rec["source_url"],)).fetchone()
    if row:
        old_id, root = row[0], (row[1] or row[0])
        ver = conn.execute("SELECT COALESCE(MAX(version),1) FROM knowledge_items WHERE root_id=?",
                           (root,)).fetchone()[0]
        conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=? WHERE id=?", (now, old_id))
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,root_id,parent_id,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rec["org_id"], rec["type"], rec["title"], rec["content"],
             json.dumps(rec["metadata"]), rec["source_url"], ver + 1, root, old_id, "crawler"))
        return "updated"
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,created_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (rec["org_id"], rec["type"], rec["title"], rec["content"],
         json.dumps(rec["metadata"]), rec["source_url"], "crawler"))
    return "inserted"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write to DB (default: dry run)")
    ap.add_argument("--limit", type=int, default=3, help="max profiles to crawl")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()

    print(f"Discovering CS faculty from {FACULTY_LIST} ...")
    profiles = discover()
    print(f"  {len(profiles)} profile links found; crawling up to {args.limit} "
          f"(delay {args.delay}s)\n")

    records = []
    for url in profiles[:args.limit]:
        try:
            p = parse_profile(url, fetch(url))
            records.append(build_record(p))
            print("=" * 72)
            print(f"{p['name'] or '(no name)'}   [{p['role']}]   {p['department'] or '(dept?)'}")
            print(f"  email : {p['email']}")
            print(f"  source: {url}")
            print(f"  research: {p['research'][:140] or '(none found)'}")
            print(f"  publications: {len(p['publications'])} extracted (showing 2):")
            for pub in p["publications"][:2]:
                print(f"     - {pub[:130]}")
            if p["links"]:
                print(f"  links : {p['links']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! failed {url}: {exc}")
        time.sleep(args.delay)

    if args.commit:
        conn = sqlite3.connect(str(DB))
        results = [upsert(conn, r) for r in records]
        conn.commit(); conn.close()
        print(f"\nCOMMITTED to {DB.name}: "
              f"{results.count('inserted')} inserted, {results.count('updated')} updated "
              f"(org {CS_ORG_ID} = CS).")
        print("Next: python v2/scripts/rebuild_index.py   (embed + index for search)")
    else:
        print(f"\nDRY RUN -- nothing written. {len(records)} record(s) would be upserted "
              f"as contacts under org {CS_ORG_ID} (CS).")
        print("Review the output, then re-run with --commit.")


if __name__ == "__main__":
    main()
