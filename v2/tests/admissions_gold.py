"""Frozen {question -> gold token} for the admissions (A/B/C) gate. Token is a stable string
that must appear in a top-2 reranked chunk. Queries are student-phrased."""

ADM_GOLD = {
    "how do I apply to a graduate program at NJIT": "graduate application",
    "what is the graduate application fee": "$75",
    "what documents do I need for my graduate application": "Transcripts",
    "how many recommendation letters does a PhD application need": "Three letters of recommendation",
    "is a statement of purpose required for the PhD": "Statement of purpose",
    "can I apply to more than one graduate program at once": "one degree program",
    "is the GRE required for admission": "GRE",
    "is the GRE required for a computing PhD": "required for all PhD applicants",
    "what is the minimum IELTS score for graduate admission": "6.5",
    "does NJIT accept Duolingo for admission": "Duolingo",
    "how do I check my application status": "connect.njit.edu/apply",
    "how do I switch to a different graduate program": "one full year",
    "can I take graduate courses before being admitted": "non-matriculated",
    "what is the collaborative PhD program": "Collaborative",
}

# No-regression guard (incl. international/office, so admissions doesn't cannibalize them).
GUARD = {
    "how do I apply for OPT": "Optional Practical Training",
    "who do I contact about a billing hold": "Bursar",
    "what is the maximum GSA travel award": "maximum of $900",
    "who are the GSA officers": "officer",
}
