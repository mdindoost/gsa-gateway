"""Frozen {question -> gold token} for the international (D+L) gate. Token is a stable string
that must appear in a top-2 reranked chunk. Queries are student-phrased."""

INTL_GOLD = {
    "how do I apply for CPT": "Curricular Practical Training",
    "what is curricular practical training": "Curricular Practical Training",
    "how do I apply for OPT before graduation": "Optional Practical Training",
    "can I work after I finish my degree on OPT": "Optional Practical Training",
    "what is STEM OPT": "STEM OPT",
    "how do I get a 24 month STEM extension": "STEM OPT",
    "how do I request a new I-20 from OGI": "I-20",
    "what financial documents do I need for my I-20": "Financial Statement",
    "what is the SEVIS fee": "SEVIS",
    "how do I transfer my SEVIS record to another school": "SEVIS",
    "can F-1 students work on campus": "on-campus",
    "how many hours can I work on campus": "20 hours",
    "how do I keep my F-1 status": "F-1 status",
    "how many credits do I need as an international graduate student": "9 credits",
}

# Overlap guard: the office pilot must still own the OPT *job search*; OGI owns OPT *application*.
OVERLAP = {
    "who do I contact about my OPT job search": "Career Development",
    "how do I apply for OPT": "Optional Practical Training",
}

# No-regression guard.
GUARD = {
    "what is the maximum GSA travel award": "maximum of $900",
    "who do I contact about a billing hold": "Bursar",
    "who are the GSA officers": "officer",
}
