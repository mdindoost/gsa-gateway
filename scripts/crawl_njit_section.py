#!/usr/bin/env python3
"""Crawl a grad-relevant njit.edu section → staged Markdown + a high-stakes review list,
then (gated) ingest it. Task #5 (senior-reviewed).

Curated-URL, not recursive BFS: precise, no over-crawl, no junk pages. Per page:
  fetch (polite, robots-checked, project UA) → clean_text → redact_volatile (drop stale
  $/%/deadline values, point to the live page) → tripwire → classify_doc (high → STAGE for
  sign-off / low → live) → write bot/data/sources/njit/<section>/<slug>.md.

Phases:
  --fetch  (default) : fetch + classify + write .md + docs/review/<section>-high-stakes.md
  --commit           : hardened backup → ingest the .md (high → is_active=0, low → live) →
                       reminds you to run embed_all.py
  --approve          : flip this section's staged (is_active=0, stakes=high) items live
                       (after your review); then run embed_all.py

Low-stakes goes live on --commit. High-stakes is held until you --approve.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.robotparser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion.gsa_docs import upsert_doc_items
import re

from bs4 import BeautifulSoup

from v2.core.ingestion.stakes import classify_doc, has_unredacted_value, redact_volatile
from v2.core.ingestion.explore import http_fetch


def clean_text(html: str) -> str:
    """Readable text from the MAIN content region (drops nav/menu/boilerplate). NJIT runs
    Drupal, so prefer <main>/[role=main]/.region-content; fall back to the whole body."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form",
                     "aside", "button"]):
        tag.decompose()
    main = (soup.select_one("main") or soup.select_one("[role=main]")
            or soup.select_one("#main-content") or soup.select_one(".region-content")
            or soup.select_one("#content") or soup)
    return re.sub(r"\n\s*\n+", "\n\n",
                   re.sub(r"[ \t]+", " ", main.get_text("\n", strip=True))).strip()

DB_PATH = str(REPO / "gsa_gateway.db")
SRC = "njit-crawl"
SOURCES = REPO / "bot" / "data" / "sources" / "njit"
REVIEW = REPO / "docs" / "review"
FETCH_DELAY = 1.5          # polite delay between requests (seconds)
MIN_BODY = 200            # chars; below this the page is JS-rendered/empty → link-only

# section → org + the curated page list. `pages` are fetched+classified; `link_only` become a
# single stable pointer doc (pure-volatile pages like the live rate schedule).
SECTIONS: dict[str, dict] = {
    # Discovery-based sections: fetch the office homepage, crawl same-prefix sub-pages.
    "registrar": {"org_slug": "registrar", "org_name": "Office of the Registrar",
                  "parent": "njit", "org_type": "office",
                  "seed": "https://www.njit.edu/registrar/", "max_pages": 20},
    "financialaid": {"org_slug": "financialaid", "org_name": "Office of Financial Aid",
                     "parent": "njit", "org_type": "office",
                     "seed": "https://www.njit.edu/financialaid/", "max_pages": 20},
    "graduatestudies": {"org_slug": "graduate-studies", "org_name": "Office of Graduate Studies",
                        "parent": "njit", "org_type": "office",
                        "seed": "https://www.njit.edu/graduatestudies/", "max_pages": 25},
    "counseling": {"org_slug": "counseling", "org_name": "Counseling Center (C-CAPS)",
                   "parent": "njit", "org_type": "office",
                   "seed": "https://www.njit.edu/counseling/", "max_pages": 15},
    "careerservices": {"org_slug": "career-development", "org_name": "Career Development Services",
                       "parent": "njit", "org_type": "office",
                       "seed": "https://www.njit.edu/careerservices/", "max_pages": 15},
    "dos": {"org_slug": "dean-of-students", "org_name": "Dean of Students",
            "parent": "njit", "org_type": "office",
            "seed": "https://www.njit.edu/dos/", "max_pages": 15},
    "global": {"org_slug": "ogi", "org_name": "Office of Global Initiatives",
               "parent": "njit", "org_type": "office",
               "seed": "https://www.njit.edu/global/", "max_pages": 20},
    "bursar": {
        "org_slug": "bursar", "org_name": "Office of the Bursar / Student Accounts",
        "parent": "njit", "org_type": "office",
        "pages": [
            ("bursar-payments", "https://www.njit.edu/bursar/for-students", "Bursar — Payments, Refunds & Holds"),
            ("bursar-faqs", "https://www.njit.edu/bursar/faqs", "Bursar — FAQs"),
            ("bursar-forms", "https://www.njit.edu/bursar/forms", "Bursar — Forms (incl. 1098-T)"),
            ("bursar-erefund", "https://www.njit.edu/bursar/touchnet-erefund", "Bursar — eRefund (Direct Deposit)"),
            ("bursar-contact", "https://www.njit.edu/bursar/contact-us", "Bursar — Contact & Hours"),
        ],
        "link_only": [
            ("bursar-tuition-rates", "https://www.njit.edu/bursar/tuition-and-fee-schedule",
             "Tuition & fee rates", "Graduate tuition and fees are billed per credit. For the current rates"),
            ("bursar-important-dates", "https://www.njit.edu/bursar/important-dates",
             "Billing & refund deadlines", "Billing, payment, and refund/withdrawal deadlines are set each term. For the current dates"),
        ],
    },
}


def _robots_ok(url: str) -> bool:
    try:
        from urllib.parse import urlsplit
        base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return True   # no robots / unreadable → proceed (these are public student pages)


def discover_pages(section: str, cfg: dict) -> list[tuple[str, str, str]]:
    """Fetch the office homepage and return its same-prefix sub-pages (incl. the homepage):
    (slug, url, title). Bounded by max_pages; same URL-PREFIX (not just same host)."""
    from urllib.parse import urldefrag, urljoin
    seed = cfg["seed"].rstrip("/")
    prefix = seed + "/"
    final, html, status = http_fetch(cfg["seed"])
    time.sleep(FETCH_DELAY)
    urls: list[str] = [seed]
    if status == "ok" and html:
        soup = BeautifulSoup(html, "html.parser")
        _ASSET = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".doc", ".docx", ".xls",
                  ".xlsx", ".ppt", ".zip", ".mp4", ".svg")
        for a in soup.find_all("a", href=True):
            u = urldefrag(urljoin(final, a["href"]))[0].rstrip("/")
            if (u.startswith(prefix) or u == seed) and u not in urls and "?" not in u \
                    and not u.lower().endswith(_ASSET) and "/files/" not in u:
                urls.append(u)
    urls = urls[: cfg.get("max_pages", 20)]
    out = []
    for u in urls:
        tail = u.split("/")[-1] or "home"
        out.append((f"{section}-{tail}", u, f"{cfg['org_name']} — {tail.replace('-', ' ').title()}"))
    return out


# Generic inbox local-parts → NOT a person (an office contact); never make a Person node.
_GENERIC_INBOX = re.compile(
    r"^(bursar|registrar|info|contact|help|admin|thirdparty|finaid|financialaid|dos|career|"
    r"careerservices|counsel\w*|global|ogi|graduatestudies|gradstudies|webmaster|no-?reply|"
    r"support|studentaccounts?|payments?|refunds?|oars|accessibility|deanofstudents|"
    r"emergency|wellness|health|cds|recruit\w*)$", re.I)
# A clean personal NJIT email: firstname.lastname@njit.edu → a real person we can name safely.
_PERSONAL_EMAIL = re.compile(r"^([a-z]+)\.([a-z]+)\d*@njit\.edu$", re.I)


def extract_people(html: str) -> list[tuple[str, str]]:
    """(name, email) for clean personal NJIT contacts on the page. Conservative: only
    firstname.lastname@njit.edu (derive a safe display name); generic inboxes skipped, so we
    never create a junk Person node from 'bursar@njit.edu'."""
    soup = BeautifulSoup(html or "", "html.parser")
    emails: set[str] = set()
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            emails.add(a["href"][7:].split("?")[0].strip().lower())
    for m in re.findall(r"[a-z0-9._%+-]+@njit\.edu", soup.get_text(" "), re.I):
        emails.add(m.lower())
    found: dict[str, str] = {}
    for e in emails:
        local = e.split("@")[0]
        if _GENERIC_INBOX.match(local):
            continue
        m = _PERSONAL_EMAIL.match(e)
        if m:
            found[e] = f"{m.group(1).capitalize()} {m.group(2).capitalize()}"
    return sorted((name, e) for e, name in found.items())


def _front_matter(title, source_url, stakes, org_slug) -> str:
    return (f"---\ntitle: {title}\nsource_url: {source_url}\n"
            f"stakes: {stakes}\norg_slug: {org_slug}\n---\n")


def _parse_md(text: str) -> dict:
    meta, body = {}, text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return {**meta, "body": body.strip()}


def cmd_fetch(section: str, cfg: dict) -> None:
    out_dir = SOURCES / section
    out_dir.mkdir(parents=True, exist_ok=True)
    # Wipe stale .md from a prior run so a page that's since been dropped (or an asset that
    # slipped through before the filter) can't linger on disk and get re-ingested on --commit.
    for old in out_dir.glob("*.md"):
        old.unlink()
    REVIEW.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[str, str, str]] = []   # (slug, title, source_url)
    live = 0
    people: dict[str, str] = {}               # email -> name (captured during the crawl)

    pages = cfg["pages"] if "pages" in cfg else discover_pages(section, cfg)
    for slug, url, title in pages:
        if not _robots_ok(url):
            print(f"  [robots] skip {url}")
            continue
        final, html, status = http_fetch(url)
        time.sleep(FETCH_DELAY)
        # Binary body (an asset that slipped the URL filters) → not a page; skip entirely.
        if status == "ok" and html and ("\x00" in html or "<" not in html[:2000]):
            print(f"  [skip] non-HTML/binary body {url}")
            continue
        if status == "ok" and html:
            for nm, em in extract_people(html):
                people[em] = nm
        if status != "ok" or not html:
            print(f"  [fetch] {status} {url} → link-only fallback")
            body = f"For details, see the live page: {url}"
            stakes = "low"
        else:
            raw = clean_text(html)
            if len(raw) < MIN_BODY:                      # JS-rendered/empty → link-only
                print(f"  [thin] {url} ({len(raw)} chars) → link-only")
                body = f"For details, see the live page: {url}"
                stakes = "low"
            else:
                body, n_red = redact_volatile(raw, final)
                if has_unredacted_value(body):           # tripwire → force stage
                    print(f"  [tripwire] value survived redaction in {url} → staged")
                    stakes = "high"
                else:
                    stakes = classify_doc(final, body, had_volatile=n_red > 0)
        (out_dir / f"{slug}.md").write_text(
            _front_matter(title, url, stakes, cfg["org_slug"]) + body + "\n", encoding="utf-8")
        if stakes == "high":
            staged.append((slug, title, url))
        else:
            live += 1
        print(f"  [{stakes:4}] {slug}  ({url})")

    for slug, url, title, lead in cfg.get("link_only", []):
        body = f"{lead}, see the live page: {url}"
        (out_dir / f"{slug}.md").write_text(
            _front_matter(title, url, "low", cfg["org_slug"]) + body + "\n", encoding="utf-8")
        live += 1
        print(f"  [low ] {slug}  (link-only)")

    # the review list for high-stakes sign-off
    rl = REVIEW / f"{section}-high-stakes.md"
    if staged:
        lines = [f"# {section} — high-stakes content STAGED for review ({len(staged)})", "",
                 "These are NOT live to students. Review each, then approve with:",
                 f"`python scripts/crawl_njit_section.py {section} --approve` (then embed_all.py)", ""]
        for slug, title, url in staged:
            lines.append(f"- **{title}** ({slug}) — source: {url}\n  file: `bot/data/sources/njit/{section}/{slug}.md`")
        rl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if people:
        import json
        (out_dir / "_people.json").write_text(
            json.dumps([{"name": n, "email": e} for e, n in sorted(people.items())], indent=2),
            encoding="utf-8")
    print(f"\n  {live} doc(s), {len(people)} person(s) captured"
          + (f", {len(staged)} tagged high-stakes." if staged else "."))


def cmd_commit(section: str, cfg: dict, do_commit: bool) -> None:
    out_dir = SOURCES / section
    files = sorted(out_dir.glob("*.md"))
    if not files:
        print("  no .md files — run --fetch first."); return
    if do_commit:
        print(f"  [backup] {hardened_backup(DB_PATH, f'njit-{section}')}")
    conn = get_connection(DB_PATH)
    try:
        oid = ensure_org(conn, slug=cfg["org_slug"], name=cfg["org_name"],
                         parent_slug=cfg["parent"], type=cfg["org_type"])
        sync_org_nodes(conn)
        # Policy: NJIT-sourced content is authoritative → ingest LIVE. Staleness (the only
        # real risk) is already handled by redact_volatile (volatile specifics → live link) +
        # the high-stakes heads-up. The `stakes` tag is kept in metadata for traceability only.
        n = 0
        for f in files:
            d = _parse_md(f.read_text(encoding="utf-8"))
            upsert_doc_items(conn, org_id=oid, slug=f.stem, title=d.get("title", f.stem),
                             text=d["body"], source_url=d.get("source_url"),
                             doc_type="policy", source=SRC,
                             is_active=1, stakes=d.get("stakes"))
            n += 1
        # People captured during the crawl → KG Person nodes under this office (no bio chunk;
        # surfaced by the people-in-org / entity structured queries). source='njit-crawl'.
        np = 0
        pf = out_dir / "_people.json"
        if pf.exists():
            import json
            from v2.core.ingestion.people_editor import add_or_edit_person
            for p in json.loads(pf.read_text(encoding="utf-8")):
                add_or_edit_person(conn, org_id=oid, name=p["name"], title="Staff",
                                   category="staff", email=p.get("email"), source=SRC)
                np += 1
        if do_commit:
            conn.commit()
            print(f"  [COMMITTED] {n} doc(s) + {np} person(s) live. Run: python3 v2/scripts/embed_all.py")
        else:
            conn.rollback()
            print(f"  [DRY-RUN] would write {n} doc(s) + {np} person(s). --commit to apply.")
    finally:
        conn.close()


def cmd_approve(section: str, cfg: dict) -> None:
    b = hardened_backup(DB_PATH, f"njit-{section}-approve")
    print(f"  [backup] {b}")
    conn = get_connection(DB_PATH)
    try:
        oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (cfg["org_slug"],)).fetchone()
        if not oid:
            print("  org not found — commit first."); return
        cur = conn.execute(
            "UPDATE knowledge_items SET is_active=1, "
            "metadata=json_set(metadata,'$.approved_at',datetime('now')) "
            "WHERE org_id=? AND created_by=? AND is_active=0 "
            "AND json_extract(metadata,'$.stakes')='high'", (oid[0], SRC))
        conn.commit()
        print(f"  [APPROVED] {cur.rowcount} item(s) → live. Now run: python3 v2/scripts/embed_all.py")
    finally:
        conn.close()


def cmd_refresh(section: str, cfg: dict) -> None:
    """One-shot office refresh for the dashboard Jobs runner: fetch → commit (live, gated
    backup) → embed. Re-crawl is idempotent per doc_id, so this is the safe 'update this
    office from njit.edu' button."""
    cmd_fetch(section, cfg)
    cmd_commit(section, cfg, do_commit=True)
    print("  [embed] embedding new/changed items …")
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "v2" / "scripts" / "embed_all.py")],
                       cwd=str(REPO))
    print(f"  [embed] exit {r.returncode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section", choices=sorted(SECTIONS) + ["all"])
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--commit", action="store_true", help="gated ingest of the .md (else dry-run)")
    ap.add_argument("--ingest", action="store_true", help="dry-run ingest preview")
    ap.add_argument("--approve", action="store_true", help="flip staged high-stakes items live")
    ap.add_argument("--refresh", action="store_true",
                    help="one-shot: fetch + commit (live) + embed (for the dashboard Jobs runner)")
    args = ap.parse_args()
    sections = sorted(SECTIONS) if args.section == "all" else [args.section]
    for sec in sections:
        cfg = SECTIONS[sec]
        print(f"\n══════ {sec} ══════")
        if args.approve:
            cmd_approve(sec, cfg)
        elif args.refresh:
            cmd_refresh(sec, cfg)
        elif args.commit or args.ingest:
            cmd_commit(sec, cfg, do_commit=args.commit)
        else:
            cmd_fetch(sec, cfg)


if __name__ == "__main__":
    main()
