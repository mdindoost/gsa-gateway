"""Frozen question -> gold fact substring maps for the rerank acceptance gate.

A question 'passes' when a retrieved chunk's content CONTAINS the substring. Substrings
are id-stable (survive re-ingest). GOLD = the wrong-chunk misses the reranker must fix;
GUARD = already-correct questions that must not regress.
"""

GOLD = {
    "Who chairs the GSA General Assembly meetings?": "Chair the General Assembly meetings",
    "What is the minimum GPA to run for a GSA Executive Board position?": "minimum 3.00 GPA",
    "How many terms can someone serve in one GSA officer position?": "more than two terms",
    "Who are the GSA's two advisors and which offices are they from?":
        "Academic Advisor shall be a member of the Office of Graduate Studies",
    "What is the per-person food cost limit for a club event of 25 students?":
        "$9 per person for an event of 0 to 30 students",
    "How many events must a graduate club hold on campus per semester?":
        "at least 2 events on-campus per semester",
    "How much can a graduate club receive from a conference/competition grant?":
        "Organizations can receive up to a $500 grant",
    "How many days after travel must I submit the Chrome River Expense Report?":
        "within 30 days of travel",
    "Are AirBNB stays reimbursable under the GSA travel award?":
        "AirBNBs, VRBOs, or other vacation rentals are not eligible",
    "How is the GSA Vice President for Academic Affairs selected?":
        "nominate and appoint the Vice President for Academic Affairs",
    "Who can impeach a GSA officer and what vote is needed?":
        "two-thirds majority vote of all the department representatives present",
}

GUARD = {
    "What is the maximum GSA travel award per fiscal year?": "maximum of $900",
    "How much is an asset grant for a graduate club?": "up to a $150 grant",
    "What percentage of a club's budget can be spent on prizes?":
        "15% of their whole budget on prizes",
    "Can a club use petty cash reimbursement?": "Petty cash reimbursement will NOT",
    "What happens to a club on its 2nd financial bylaw offense?":
        "10% off their original budget",
    "What is the IRS mileage rate used for GSA travel reimbursement?": "$0.70 per mile",
    "What cumulative GPA must a CS PhD student maintain?": "at least 3.5",
    "Who sits on the CS PhD Qualifying Exam Committee?": "three tenure-track NJIT faculty",
    "Where is the GSA office located and what are its hours?": "Campus Center 110A",
    "When does the GSA fiscal year run?": "July 1st through June 30th",
}
