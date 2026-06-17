from v2.core.ingestion.section_chunker import chunk_markdown

DOC = """# GSA Constitution

## Section I: Overview

### Advisors
The GSA Academic Advisor shall be a member of the Office of Graduate Studies.

## Section II: Executive Board

### Responsibilities

#### President
Convene and preside over all Executive Board meetings.

#### Vice President of Finance
Manage the social and cultural budget for the GSA.
"""


def test_each_subsection_is_its_own_chunk():
    chunks = chunk_markdown(DOC, max_tokens=320)
    # Advisors, President, and VP Finance facts must NOT be merged together.
    advisors = [c for c in chunks if "Office of Graduate Studies" in c]
    pres = [c for c in chunks if "preside over all Executive Board" in c]
    fin = [c for c in chunks if "social and cultural budget" in c]
    assert len(advisors) == 1 and len(pres) == 1 and len(fin) == 1
    # They are three distinct chunks (no cross-contamination).
    assert advisors[0] != pres[0] != fin[0]
    assert "social and cultural budget" not in pres[0]
    assert "preside over" not in fin[0]


def test_chunk_carries_heading_path_for_context():
    chunks = chunk_markdown(DOC, max_tokens=320)
    pres = next(c for c in chunks if "preside over all Executive Board" in c)
    # The chunk should know it is the President under Section II / Responsibilities.
    assert "President" in pres
    assert "Executive Board" in pres


def test_long_section_splits_but_keeps_heading():
    body = " ".join(f"Sentence number {i} about budgets and funding." for i in range(200))
    doc = f"## Big Section\n{body}"
    chunks = chunk_markdown(doc, max_tokens=80)
    assert len(chunks) > 1
    assert all("Big Section" in c for c in chunks)
