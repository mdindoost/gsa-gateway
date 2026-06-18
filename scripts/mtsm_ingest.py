#!/usr/bin/env python3
"""Ingest the Martin Tuchman School of Management (MTSM) into the KG + KB.

Mirrors the YWCC pattern: a college org under NJIT, a program sub-org, college-level
people (Dean + program director), and decomposed KB prose (programs, PhD admission,
FAQ). All manual content is source='dashboard' so the crawler reconcile never touches it.

GATED: default dry-run. Pass --commit to take a hardened backup and write the live DB.
After --commit, run: python3 v2/scripts/embed_all.py

Sources (verbatim primary docs):
  https://management.njit.edu/administration
  https://management.njit.edu/academics/graduate
  https://management.njit.edu/phd-program
  https://management.njit.edu/admission-requirements
  https://management.njit.edu/frequently-asked-questions-faq
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes

DB_PATH = str(REPO / "gsa_gateway.db")
SRC = "dashboard"

ADMIN = "https://management.njit.edu/administration"
GRAD = "https://management.njit.edu/academics/graduate"
PHD = "https://management.njit.edu/phd-program"
ADM = "https://management.njit.edu/admission-requirements"
FAQ = "https://management.njit.edu/frequently-asked-questions-faq"

# ── Orgs ────────────────────────────────────────────────────────────────────────
# (slug, name, parent_slug, type)
ORGS = [
    ("mtsm", "Martin Tuchman School of Management (MTSM)", "njit", "college"),
    ("business-data-science", "Business Data Science", "mtsm", "program"),
]
MTSM_ALIASES = [
    "Martin Tuchman School of Management", "MTSM", "Tuchman",
    "Tuchman School", "Tuchman School of Management", "School of Management",
]

# ── People ──────────────────────────────────────────────────────────────────────
# NONE here on purpose. MTSM people (Dean, faculty, advisors, staff) are owned by the
# CRAWLER (source='crawler') via the MTSM entry points in entry_points.py + run_explore.py,
# fully profile-enriched and re-crawl-maintained. Adding them here too would create duplicate
# Person nodes (M3 in the design review). This script writes ONLY orgs + KB prose.

# ── KB docs ─────────────────────────────────────────────────────────────────────
# Each: dict(org, type, title, content, source_url, key)
KB: list[dict] = [
    dict(org="mtsm", type="about", key="mtsm:about:grad-overview", source_url=GRAD,
         title="Martin Tuchman School of Management — Graduate Programs",
         content=(
             "A Master's Program with the Power of Technology. The Martin Tuchman School of "
             "Management (MTSM) at NJIT offers several graduate program options to advance your "
             "learning and career: a Master of Science in Management (MSM) and a Master of "
             "Business Administration (MBA) — both available online and on campus — plus a "
             "variety of Graduate Certificate Programs, and a Ph.D. in Business Data Science.")),
    dict(org="mtsm", type="about", key="mtsm:about:msm", source_url=GRAD,
         title="Master of Science in Management (MSM)",
         content=(
             "The Master of Science in Management (MSM) program is a 30-credit STEM-designated "
             "program designed for successful professionals facing the prospect of moving into "
             "managerial positions as the next logical step in their career progression. It is "
             "more focused than the MBA curriculum, with a stronger emphasis on mastery of a "
             "clearly defined concentration area. The MSM is best suited for candidates who wish "
             "to have more influence in their organizations by moving into managerial positions "
             "but who also desire to retain their allegiance to an area of technical expertise.")),
    dict(org="mtsm", type="about", key="mtsm:about:mba", source_url=GRAD,
         title="TECH MBA — Master of Business Administration",
         content=(
             "The Martin Tuchman School of Management's TECH MBA program is a 36-credit "
             "STEM-designated program decisively about the empowerment of the technology-focused "
             "professionals who enroll in it. Built on the principle that the 21st-century "
             "organization will increasingly depend on the management of emerging technology at "
             "all levels, the program emphasizes the strategic implications of technology for "
             "businesses. It infuses industry-leading research with the practical application of "
             "new technology, capitalizing on NJIT's technology-focused heritage as a leading "
             "public polytechnic. It is offered for full-time study and as a part-time program "
             "with evening and online courses. The Online MBA gives students the flexibility to "
             "succeed in today's rapidly evolving global business landscape; over four semesters, "
             "tech-savvy management professionals develop the decision-making and problem-solving "
             "skills needed to strategically leverage technology, manage innovation, and operate "
             "as engaged leaders.")),

    dict(org="business-data-science", type="about", key="bds:about:overview", source_url=PHD,
         title="Ph.D. in Business Data Science — Program Overview",
         content=(
             "The Martin Tuchman School of Management offers a Ph.D. in Business Data Science to "
             "prepare the next generation of data scientists for business and management. Business "
             "Data Science is an emerging, rapidly growing, interdisciplinary field; this program "
             "is the first of its kind in the United States. It integrates business analytics with "
             "scientific methods from statistics, computer science, and engineering to improve "
             "knowledge discovery and decision-making in business areas. The program trains Ph.D. "
             "students to conduct innovative, independent, impactful, and quantitative research in "
             "business disciplines, including but not limited to Finance, Accounting, Operations "
             "Management, Marketing, and Innovation Management. Students are encouraged to publish "
             "in academic conferences and scholarly journals and have the opportunity to teach in "
             "the undergraduate program. The program welcomes exceptional students with a "
             "bachelor's degree or higher in business, engineering, computer science, mathematics, "
             "or other relevant disciplines. Graduates become academics, researchers, business "
             "data scientists, corporate leaders, and policy-makers. It is a STEM-designated "
             "program.")),

    dict(org="business-data-science", type="policy", key="bds:adm:season-deadline", source_url=ADM,
         title="Ph.D. in Business Data Science — Admission Season & Deadlines",
         content=(
             "Admission to the Ph.D. program in Business Data Science is for the Fall semester "
             "only. The next admission season is Fall 2026. The application deadline is December "
             "15 in the prior calendar year (December 15, 2025). As admission is competitive, "
             "applicants are strongly advised to complete their applications before the deadline. "
             "After the deadline, applications may be considered on a rolling basis subject to "
             "availability until February 15, 2026, though this is not guaranteed. Submitting an "
             "application does not guarantee admission. Admitted students may be required to place "
             "a deposit to confirm their intent to enroll. International admitted students should "
             "contact the Office of Global Initiatives for visa applications.")),
    dict(org="business-data-science", type="policy", key="bds:adm:checklist", source_url=ADM,
         title="Ph.D. in Business Data Science — Application Checklist",
         content=(
             "Application checklist for the MTSM Ph.D. in Business Data Science: Online "
             "application; Official transcripts; CV or Resume; Statement of Purpose; Official GMAT "
             "or GRE; Official TOEFL, IELTS, DUOLINGO or PTE (if applicable); Three Recommendation "
             "Letters; Course Mapping Profile; and a non-refundable Application Fee. Submit "
             "application materials at NJIT's official application website. The current application "
             "fee is $75, payable online during the application process.")),
    dict(org="business-data-science", type="policy", key="bds:adm:requirements", source_url=ADM,
         title="Ph.D. in Business Data Science — Detailed Admission Requirements",
         content=(
             "Prospective applicants are expected to have a strong quantitative background, some "
             "software development experience or computational skills, and a basic understanding "
             "of business principles. Official transcripts and proof of degree completion from all "
             "colleges and universities attended are required. The minimum admission requirement "
             "is a bachelor's degree (or equivalent) in business, computer science, engineering, "
             "mathematics, or other relevant disciplines from an accredited institution, with a "
             "minimum overall GPA of 3.2 out of 4.0; a master's degree is not required, though "
             "many applicants hold one. Either GMAT or GRE is required (valid up to five years, "
             "and must not be expired at the December 15 deadline). International applicants whose "
             "first language is not English must submit TOEFL, IELTS, DUOLINGO or PTE (valid two "
             "years); exemptions may be granted to applicants who have earned (or will earn before "
             "enrolling) a U.S. degree, or a degree from a recognized institution in a country "
             "where all instruction is in English. Three recent recommendation letters are "
             "required, at least one from academia (e.g., a professor). A Course Mapping Profile "
             "is required to examine qualifications on bridge courses (all applicants) and core "
             "courses (applicants with a master's degree). The Statement of Purpose should cover "
             "reasons for applying, qualifications, research interests and their alignment with "
             "MTSM faculty scholarship, research experience, and whether the applicant requests "
             "financial support or will self-fund.")),
    dict(org="business-data-science", type="policy", key="bds:adm:background", source_url=ADM,
         title="Ph.D. in Business Data Science — Recommended Background & Bridge Courses",
         content=(
             "Prepared students should have background in programming and data structures (e.g., "
             "NJIT CS 280 or CS 5050), advanced Calculus (e.g., NJIT MATH 211), Probability and "
             "Statistics (e.g., NJIT MGMT 216 or MATH 333), and basic business knowledge (e.g., "
             "NJIT MGMT 492 or MGMT 501). Admitted students lacking competencies in one or more of "
             "these areas must consult with the program director to take relevant bridge courses.")),
    dict(org="business-data-science", type="contact", key="bds:contact:director", source_url=FAQ,
         title="Ph.D. in Business Data Science — Program Director Contact",
         content=(
             "For questions about the MTSM Ph.D. in Business Data Science, contact the program "
             "director, Dr. Ming Taylor, at ming.f.taylor@njit.edu. Use the email subject "
             "'Business Data Science Program Inquiry'. Due to high volume, the program does not "
             "reply to questions already answered in the FAQ; please allow a week before sending a "
             "reminder.")),
]

# FAQ — each entry becomes its own focused faq knowledge_item.
_FAQ_ITEMS = [
    ("When is the next admission season for the Business Data Science Ph.D.?",
     "Admission to the Ph.D. program in Business Data Science is for the Fall semester only. The "
     "next admission season is Fall 2026, and the application deadline is December 15, 2025."),
    ("Is a GMAT or GRE score required for the Business Data Science Ph.D.?",
     "Yes. Either a GMAT or a GRE score is acceptable. GMAT and GRE are valid for up to five "
     "years and cannot be expired at the time of the application deadline (December 15); expired "
     "scores will not be considered."),
    ("Do I need official GMAT/GRE scores and official transcripts from all schools attended?",
     "Yes. Official GMAT/GRE scores and official transcripts from all colleges and universities "
     "attended are required by the institution."),
    ("Do I need a TOEFL/IELTS score for the Business Data Science Ph.D.?",
     "International applicants must demonstrate English proficiency per the NJIT admission "
     "standard. TOEFL, IELTS, DUOLINGO or PTE is required if English is not the applicant's first "
     "language. Exemptions may be granted to applicants who have earned (or will earn before "
     "enrolling) a U.S. bachelor's, master's, or doctoral degree, or a degree from a recognized "
     "institution in a country where all instruction is in English."),
    ("What are the degree requirements for the Business Data Science Ph.D.?",
     "Degree requirements and the program catalog are available on the MTSM Ph.D. program pages."),
    ("Am I expected to publish papers to obtain the Ph.D. degree?",
     "Yes. Students are expected to conduct innovative, independent research and publish their "
     "findings in peer-reviewed scholarly journals and/or academic conference proceedings."),
    ("Is the Business Data Science Ph.D. a STEM-designated program?",
     "Yes, the Ph.D. in Business Data Science is a STEM-designated degree program."),
    ("What is the cost of pursuing the Business Data Science Ph.D.?",
     "Tuition and fees depend on the number of courses a student needs to register for to meet "
     "the degree requirements. Refer to NJIT's tuition and fees information for details."),
    ("Is there any financial support for the Business Data Science Ph.D.?",
     "Competitive merit-based financial support may be available for Ph.D. students who commit to "
     "being full-time, via teaching assistantships, research assistantships, fellowships, etc. "
     "Support may cover tuition, fees, and a stipend at a University-set rate; some fellowships "
     "provide a higher stipend. Indicate in your statement of purpose whether you are applying for "
     "support or are self-funded. Applications are reviewed for support until positions are filled."),
    ("What is my likelihood of being admitted to the Business Data Science Ph.D.?",
     "Admission decisions depend on many factors, such as the number of applications, applicants' "
     "credentials, and funding availability, so no admission 'preview' can be offered. A committee "
     "of several MTSM faculty thoroughly reviews all complete applications. Those who meet the "
     "admission requirements are encouraged to apply."),
    ("Do you accept part-time students into the Business Data Science Ph.D.?",
     "Yes. Both full-time and part-time students who are motivated to publish in reputable "
     "peer-reviewed journals and conference proceedings are admitted. Part-time applicants may "
     "consider the Collaborative Ph.D. Program."),
    ("How are Business Data Science Ph.D. courses delivered?",
     "Courses can be offered face to face, online, or in a hybrid mode, and may be scheduled "
     "during the daytime, in the evenings, or online."),
    ("What is the application deadline for the Business Data Science Ph.D.?",
     "Admission is granted only for the Fall semester. The application deadline is December 15 in "
     "the prior calendar year (December 15, 2025 for Fall 2026). After the deadline, applications "
     "may be considered on a rolling basis subject to availability until February 15, 2026, but "
     "late applications are not guaranteed to be considered."),
    ("When will my application be reviewed?",
     "Each application is reviewed only after its status is 'complete' in the NJIT application "
     "portal, which must occur by the deadline. Incomplete applications, or applications completed "
     "after the deadline, may be rejected without review."),
    ("What is my application status and when will a decision be made?",
     "Each complete application is evaluated by a committee. Applicants are notified via the NJIT "
     "application online portal as soon as a final decision is made, and also receive an email if "
     "an admission or financial support offer is made."),
    ("What if I have additional materials after a decision is made?",
     "It is the applicant's responsibility to ensure all submitted applications are accurate and "
     "complete. Once an admission decision is made, it is final. Applicants may choose to apply "
     "again in the future."),
    ("Are there other requirements for admitted students beyond program requirements?",
     "Yes. All admitted students must follow the institution's Academic Policies and Procedures "
     "and fulfill the requirements of the Office of Graduate Studies."),
]
for _i, (_q, _a) in enumerate(_FAQ_ITEMS, 1):
    # Stable, deterministic key (enumerate index) — NOT hash(_q), which is per-process salted
    # and would change every run, defeating the natural_key idempotency and duplicating FAQs.
    KB.append(dict(org="business-data-science", type="faq",
                   key=f"bds:faq:{_i:02d}", source_url=FAQ,
                   title=_q, content=f"{_q}\n\n{_a}"))


def _org_id(conn, slug):
    return conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write the DB (else dry-run)")
    ap.add_argument("--db", default=DB_PATH, help="target DB (default: live; use a copy to test)")
    args = ap.parse_args()
    db_path = args.db

    if args.commit:
        b = hardened_backup(db_path, "mtsm-ingest")
        print(f"[backup] {b}")

    conn = get_connection(db_path)
    try:
        # 1) Orgs
        for slug, name, parent, otype in ORGS:
            ensure_org(conn, slug=slug, name=name, parent_slug=parent, type=otype)
            print(f"[org]    {slug:24} {name}")
        # aliases on mtsm
        mid = _org_id(conn, "mtsm")
        meta_row = conn.execute("SELECT metadata FROM organizations WHERE id=?", (mid,)).fetchone()
        meta = json.loads(meta_row[0]) if meta_row and meta_row[0] else {}
        meta["aliases"] = MTSM_ALIASES
        conn.execute("UPDATE organizations SET metadata=? WHERE id=?", (json.dumps(meta), mid))
        print(f"[alias]  mtsm -> {MTSM_ALIASES}")

        sync_org_nodes(conn)

        # People are owned by the crawler (see entry_points.py / run_explore.py) — not here.

        # 2) KB docs (idempotent on metadata.natural_key)
        n_new = 0
        for d in KB:
            oid = _org_id(conn, d["org"])
            conn.execute(
                "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                "WHERE is_active=1 AND created_by=? AND json_extract(metadata,'$.natural_key')=?",
                (SRC, d["key"]))
            meta = json.dumps({"verified": True, "natural_key": d["key"], "source_url": d["source_url"]})
            cur = conn.execute(
                "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
                "source_url,is_active,created_by) VALUES(?,?,?,?,?,1,?,1,?)",
                (oid, d["type"], d["title"], d["content"], meta, d["source_url"], SRC))
            conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?", (cur.lastrowid, cur.lastrowid))
            n_new += 1
        print(f"[kb]     {n_new} knowledge_items written "
              f"({sum(1 for d in KB if d['type']=='faq')} faq, "
              f"{sum(1 for d in KB if d['type']=='policy')} policy, "
              f"{sum(1 for d in KB if d['type']=='about')} about, "
              f"{sum(1 for d in KB if d['type']=='contact')} contact)")

        if args.commit:
            conn.commit()
            print("\n[COMMITTED] now run:  python3 v2/scripts/embed_all.py")
        else:
            conn.rollback()
            print("\n[DRY-RUN] no changes written. Re-run with --commit to apply.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
