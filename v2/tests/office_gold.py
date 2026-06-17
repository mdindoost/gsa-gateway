"""Frozen {question -> gold office token} for the office-routing gate. The token is a stable
string (office name fragment or email) that must appear in a top-2 reranked chunk. Queries are
student-phrased, NOT copied from the doc's `Handles:` wording (avoids overfitting)."""

OFFICE_GOLD = {
    # core M intents
    "which office handles graduate admission questions": "University Admissions",
    "who do I contact about my international student visa": "Global Initiatives",
    "which office handles course registration problems": "Registrar",
    "who do I contact about my tuition bill": "Bursar",
    "which office reviews my thesis and dissertation": "Graduate Studies",
    "which office handles career fairs and internships": "Career Development",
    "who do I contact if I need disability accommodations": "Accessibility",
    "where do I get help with my NJIT email wifi and Canvas": "Service Desk",
    # adversarial overlap pairs (senior review S2)
    "who do I talk to about my OPT job search": "Career Development",
    "who handles a registration hold on my account": "Registrar",
    "who do I contact about a billing hold": "Bursar",
    "I am in a mental health crisis right now who do I contact": "Counseling",
}

# Guard set: existing non-office answers must NOT regress.
GUARD = {
    "what is the maximum GSA travel award": "maximum of $900",
    "who are the GSA officers": "officer",
    "what cumulative GPA must a CS PhD student maintain": "3.5",
}
