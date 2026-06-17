#!/usr/bin/env python
"""One-off gated capture of NJIT office staff (from official office contact pages, verified by
the maintainer) into the KG as Person nodes + has_role edges under their office org. Emails are
merged where provided. No bio knowledge_item (about=None) — these are structured directory
entities, surfaced via people-in-org structured queries, not RAG prose. source='dashboard'
(human-verified). Dry-run by default; --commit takes a hardened backup first.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import sync_org_nodes
from v2.core.ingestion.people_editor import add_or_edit_person
from v2.core.retrieval.skills import resolve_org

# (office slug, name, title, email|None)
PEOPLE: list[tuple[str, str, str, str | None]] = [
    # --- Office of University Admissions (graduate-admissions) ---
    ("graduate-admissions", "Stephen Eck", "Associate Provost of University Admissions", "eck@njit.edu"),
    ("graduate-admissions", "Dimana Kornegay", "Director of Admission and Recruitment Marketing", "neykova@njit.edu"),
    ("graduate-admissions", "Cory Cottingim", "Director of Graduate and International Admissions", "cac229@njit.edu"),
    ("graduate-admissions", "Tiffany Hartwig", "Director of Systems and Analytics", "tah44@njit.edu"),
    ("graduate-admissions", "Alberto Guichardo", "Associate Director for Transfer Recruitment and Articulation", "aguichar@njit.edu"),
    ("graduate-admissions", "Magda Francois", "Associate Director of Recruitment", "mf485@njit.edu"),
    ("graduate-admissions", "Alexis Telyczka", "Admissions Recruiter", "amt42@njit.edu"),
    ("graduate-admissions", "Danielle Drago", "Admissions Recruiter", "dld9@njit.edu"),
    ("graduate-admissions", "Yenitza Ruiz", "Admissions Recruiter", "yr64@njit.edu"),
    ("graduate-admissions", "Ariana Rivera-Maldonado", "Admissions Recruiter", "amr238@njit.edu"),
    ("graduate-admissions", "Grant Tokarski", "Admissions Recruiter", "gt259@njit.edu"),
    ("graduate-admissions", "Ashley Hunter", "Enrollment Services Manager – Graduate (Master's): NCE, Hillier, CSLA, MTSM", "ah784@njit.edu"),
    ("graduate-admissions", "Yaslie Pared", "Enrollment Services Manager – Graduate (Master's): Ying Wu College of Computing", "ymp@njit.edu"),
    ("graduate-admissions", "Simran Sawhney", "Enrollment Services Manager – Graduate (Master's) Online: Ying Wu College of Computing", "ska@njit.edu"),
    ("graduate-admissions", "Carly Hickey", "Enrollment Services Manager – Graduate (Master's) Online: NCE, Hillier, CSLA, MTSM", "ch25@njit.edu"),
    ("graduate-admissions", "Beata Anderson", "Associate Director, International Recruitment", "ba476@njit.edu"),
    ("graduate-admissions", "Erica Rolek", "Assistant Director, International Recruitment", "enr5@njit.edu"),
    ("graduate-admissions", "Tyrone Foster", "Assistant Director, International Recruitment", "tlf7@njit.edu"),
    ("graduate-admissions", "Christopher Carter", "Associate Director of Admissions Marketing and Communications", "ccarter@njit.edu"),
    ("graduate-admissions", "Shannon O'Brien", "Associate Director of Recruitment Events", "sobrien@njit.edu"),
    ("graduate-admissions", "Dave Arkais", "Assistant Director of Admissions Systems and CRM Management", "arkais@njit.edu"),
    ("graduate-admissions", "Caitlin Aristizabal", "Assistant Director of Visit Experience", "cra29@njit.edu"),
    ("graduate-admissions", "Christina McGuire", "Admissions Coordinator", "cd457@njit.edu"),
    ("graduate-admissions", "Damaris Arocho", "Administrative Assistant II", "arocho@njit.edu"),
    ("graduate-admissions", "Elizabeth Verneret", "Generalist / Data Processing Clerk", "ev232@njit.edu"),
    ("graduate-admissions", "Kamani Staggers", "Generalist / Data Processing Clerk", "ks2445@njit.edu"),
    # --- Office of Graduate Studies ---
    ("graduate-studies", "Sotirios G. Ziavras", "Vice Provost for Graduate Studies and Dean of the Graduate Faculty", "ziavras@njit.edu"),
    ("graduate-studies", "Clarisa González-Lenahan", "Director of Graduate Studies", "clarisa.gonzalez-lenahan@njit.edu"),
    ("graduate-studies", "Ester Flaim", "Assistant Director of Graduate Studies", "ester.flaim@njit.edu"),
    ("graduate-studies", "Angela Retino", "Office Manager", "aretino@njit.edu"),
    ("graduate-studies", "David Tress", "Administrative Assistant II", "david.m.tress@njit.edu"),
    ("graduate-studies", "Cortney Wortman", "Coordinator (Graduate Awards)", "wortman@njit.edu"),
    ("graduate-studies", "Maria Lirio P. Macklin", "Coordinator (Graduate Awards)", "marialirio.macklin@njit.edu"),
    # --- Office of the Dean of Students (no emails on the page) ---
    ("dean-of-students", "Marybeth Boger", "Senior Vice President of Student Affairs and Dean of Students", None),
    ("dean-of-students", "Sean Dowd", "Senior Associate Dean of Students", None),
    ("dean-of-students", "Kristie Damell", "Assistant Vice President of Institutional Access & Title IX Coordinator", None),
    ("dean-of-students", "Mark Bullock", "Associate Dean of Students", None),
    ("dean-of-students", "Rachel Williams", "Hearing & Development Officer for Student Conduct", None),
    ("dean-of-students", "Shakera Rodgers", "Executive Assistant, Dean of Students and Campus Life", None),
    ("dean-of-students", "Shyron Edwards", "Administrative Manager", None),
    # --- Career Development Services (no emails on the page) ---
    ("career-development", "Patrick Young", "Executive Director, Career Development Services", None),
    ("career-development", "Vivian Lanzot", "Director of Community & Public Service", None),
    ("career-development", "Nayelli Perez", "Director, Undergraduate Engineering Co-op", None),
    ("career-development", "Dominique Clarke", "Associate Director, Career Advising", None),
    ("career-development", "Christine Cervelli", "Associate Director, Career Advising", None),
    ("career-development", "Niasia Kennedy", "Assistant Director, Career Planning and Placement", None),
    ("career-development", "Deborah Sims", "Assistant Director", None),
    ("career-development", "Janelle Pyar", "Assistant Director, Student/Alumni Career Development", None),
    ("career-development", "Carolina Barba Granda", "Events Coordinator", None),
    ("career-development", "Casey Hennessey", "Associate Director, Employer Relations & Experiential Learning", None),
    ("career-development", "AJ Yurista", "Assistant Director, Employer Relations, On-Campus Engagement", None),
    ("career-development", "Nadyrah Amin", "CPS Program Assistant", None),
    ("career-development", "Koustubh Sahu", "Reporting and Technology Analyst", None),
    ("career-development", "Shante McNealy", "Administrative Manager", None),
    ("career-development", "Jordynn Thompson", "Administrative & Customer Service Assistant", None),
    ("career-development", "Quaniyah Smith", "Administrative & Customer Support Specialist", None),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    from collections import Counter
    by_office = Counter(p[0] for p in PEOPLE)
    print(f"{len(PEOPLE)} people to capture:")
    for office, n in by_office.items():
        print(f"   {office:<22} {n}")

    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-office-people")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    n = 0
    with conn:
        for slug, name, title, email in PEOPLE:
            org_id = resolve_org(conn, slug)
            if org_id is None:
                sys.exit(f"office org not found: {slug} (ingest offices first)")
            add_or_edit_person(conn, org_id=org_id, name=name, title=title,
                               category="staff", email=email, about=None, source="dashboard")
            n += 1
        sync_org_nodes(conn)
    print(f"captured {n} people across {len(by_office)} offices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
