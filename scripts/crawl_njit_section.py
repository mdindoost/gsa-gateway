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
import re as _re

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
    return _re.sub(r"\n\s*\n+", "\n\n",
                   _re.sub(r"[ \t]+", " ", main.get_text("\n", strip=True))).strip()

DB_PATH = str(REPO / "gsa_gateway.db")
SRC = "njit-crawl"
SOURCES = REPO / "bot" / "data" / "sources" / "njit"
REVIEW = REPO / "docs" / "review"
FETCH_DELAY = 1.5          # polite delay between requests (seconds)
MIN_BODY = 200            # chars; below this the page is JS-rendered/empty → link-only

# section → org + the curated page list. `pages` are fetched+classified; `link_only` become a
# single stable pointer doc (pure-volatile pages like the live rate schedule).
SECTIONS: dict[str, dict] = {
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
    REVIEW.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[str, str, str]] = []   # (slug, title, source_url)
    live = 0

    for slug, url, title in cfg["pages"]:
        if not _robots_ok(url):
            print(f"  [robots] skip {url}")
            continue
        final, html, status = http_fetch(url)
        time.sleep(FETCH_DELAY)
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

    for slug, url, title, lead in cfg["link_only"]:
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
    print(f"\n  {live} live doc(s), {len(staged)} STAGED for your review.")
    if staged:
        print(f"  → review list: {rl}")


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
        live = staged = 0
        for f in files:
            d = _parse_md(f.read_text(encoding="utf-8"))
            stakes = d.get("stakes", "low")
            is_active = 0 if stakes == "high" else 1
            upsert_doc_items(conn, org_id=oid, slug=f.stem, title=d.get("title", f.stem),
                             text=d["body"], source_url=d.get("source_url"),
                             doc_type="policy", source=SRC,
                             is_active=is_active, stakes=(stakes if stakes == "high" else None))
            staged += is_active == 0
            live += is_active == 1
        if do_commit:
            conn.commit()
            print(f"  [COMMITTED] {live} live, {staged} staged. Now run: python3 v2/scripts/embed_all.py")
        else:
            conn.rollback()
            print(f"  [DRY-RUN] would write {live} live + {staged} staged. --commit to apply.")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section", choices=sorted(SECTIONS))
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--commit", action="store_true", help="gated ingest of the .md (else dry-run)")
    ap.add_argument("--ingest", action="store_true", help="dry-run ingest preview")
    ap.add_argument("--approve", action="store_true", help="flip staged high-stakes items live")
    args = ap.parse_args()
    cfg = SECTIONS[args.section]
    if args.approve:
        cmd_approve(args.section, cfg)
    elif args.commit or args.ingest:
        cmd_commit(args.section, cfg, do_commit=args.commit)
    else:
        cmd_fetch(args.section, cfg)


if __name__ == "__main__":
    main()
