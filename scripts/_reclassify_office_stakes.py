#!/usr/bin/env python
"""Gated re-classification of the existing staged office_page pages (Plan 1). Applies a
human-confirmed source_url -> 'high'|'generic' map: 'generic' -> is_active=1 + drop metadata.stakes;
'high' -> is_active=1 + metadata.stakes='high'. Touches knowledge_items only (NOT office_page_state),
so the next crawl keeps skipping. Idempotent. Dry-run default; --commit takes a hardened backup.
Re-embed afterwards with v2/scripts/embed_all.py."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

_B = "https://www.njit.edu/"
# OWNER-EDITABLE confirmed decisions (full URLs). Seeded from the Plan-1 §Decision seed.
STAKES_DECISION: dict[str, str] = {
    # 'high' (genuine — payment/tuition/refund/due-dates, visa/OPT/J-1/H-1B, residency, tax):
    **{_B + p: "high" for p in [
        "bursar/Plan_Options.php", "bursar/application-payment", "bursar/critical-due-dates",
        "bursar/employee-tuition-remission", "bursar/important-dates", "bursar/international-payments",
        "bursar/payment-information", "bursar/payment-options", "bursar/payments",
        "bursar/student-payment-portal", "bursar/third-party-payments", "bursar/touchnet-erefund",
        "bursar/tuition-and-fee-schedule", "bursar/outside-scholarships", "bursar/node/71",
        "bursar/faq-payments", "bursar/faq-title-iv-authorization",
        "global/h-1b-temporary-employment-visa-0", "global/health-insurance-information-international-students",
        "global/incoming/j1-students-exchange", "global/j-2-dependent-employment/", "global/j1students",
        "global/opt-reporting", "global/tax-information-international-students",
        "global/incoming-student-information", "parking/employee-parking-fees",
        "registrar/how-residency-determined"]},
    # 'generic' (false positive -> activate):
    **{_B + p: "generic" for p in [
        "bursar/estatements", "bursar/faq/", "bursar/faqs", "bursar/node/66",
        "environmentalsafety/fire-permits", "life/", "life/gyms-facilities",
        "mailroom/amazon-lockers-njit", "mailroom/types-mail", "parking/2026-summer-hours",
        "parking/additional-njit-parking-available-essex-county-college-0",
        "parking/administrative-center-visitor-parking-494-broad-street", "parking/daily-parking-options",
        "parking/event-game-day-parking", "parking/id-cards.php", "parking/late-night-lyft-program",
        "parking/nest-research-lyft-program", "parking/parking-0", "parking/parking-rules-and-regulations",
        "parking/parking-venturelink-client-employees", "parking/rules.php",
        "parking/special-events-bus-parking", "registrar/diploma-day",
        "registrar/frequently-asked-questions"]},
}


def reclassify(conn, decisions: dict[str, str], generic_only: bool = False) -> dict:
    """Apply the decision map per source_url across all of a page's office_page chunks.

    When generic_only=True, only 'generic' decisions are applied (activate those pages).
    Every 'high' decision is skipped entirely — those rows stay is_active=0, stakes='high',
    awaiting Plan 2's verbatim-serve path before activation.
    """
    gen = hi = 0
    for url, decision in decisions.items():
        if decision == "generic":
            n = conn.execute(
                "UPDATE knowledge_items SET is_active=1, "
                "metadata=json_remove(metadata,'$.stakes'), updated_at=datetime('now') "
                "WHERE source_url=? AND type='office_page'", (url,)).rowcount
            gen += 1 if n else 0
        elif decision == "high":
            if generic_only:
                continue  # defer high activation to Plan 2
            n = conn.execute(
                "UPDATE knowledge_items SET is_active=1, "
                "metadata=json_set(metadata,'$.stakes','high'), updated_at=datetime('now') "
                "WHERE source_url=? AND type='office_page'", (url,)).rowcount
            hi += 1 if n else 0
    return {"generic_pages": gen, "high_pages": hi}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--generic-only", action="store_true",
                    help="Apply only generic (activate) decisions; skip high-stakes pages "
                         "(leave them staged for Plan 2's verbatim-serve path)")
    args = ap.parse_args(argv)
    g = sum(1 for v in STAKES_DECISION.values() if v == "generic")
    h = sum(1 for v in STAKES_DECISION.values() if v == "high")
    mode_note = " (generic-only: high-stakes pages left staged for Plan 2)" if args.generic_only else ""
    print(f"reclassify: {g} generic (activate) + {h} high (keep staged-tag) pages{mode_note}")
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-stakes-reclassify")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        print("  ", reclassify(conn, STAKES_DECISION, generic_only=args.generic_only))
    print("next: python v2/scripts/embed_all.py  (the activated generic chunks need vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
