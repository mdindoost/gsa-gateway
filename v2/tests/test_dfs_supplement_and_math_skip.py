"""DFS coverage-supplement + math-syllabus skip for the day-1 prose rebuild.

The wipe+sitemap rebuild loses DFS-only pages (pages in no sitemap): 271 college/dept-subdomain
pages + 153 www.njit.edu office-subtree pages. The supplement DFS-crawls PROSE_ENTRY_POINTS (college
subdomains) + a new SECTION_ENTRY_POINTS (www office sections, path-scoped) through the SAME canonical
write path, deduped against the sitemap rows. Separately, per-semester math course-SYLLABUS PDFs
(math.njit.edu/sites/math/files/Math_<num>-<sem>.pdf) are intentionally NOT crawled (owner 2026-07-01);
brochures/flyers/exams/quals on the same host are kept.
"""
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection, create_all
from v2.core.ingestion import college_crawl as cc


# ────────────────────────── math-syllabus predicate ──────────────────────────

def test_is_math_syllabus_true_for_course_syllabi():
    base = "https://math.njit.edu/sites/math/files/"
    for fn in ("Math_107-F18.pdf", "Math_111H-F18.pdf", "Math_105-001-F18.pdf",
               "Math%20213-S19.pdf", "math_112-su20.pdf"):
        assert cc.is_math_syllabus(base + fn) is True, fn


def test_is_math_syllabus_false_for_brochures_exams_quals():
    base = "https://math.njit.edu/sites/math/files/"
    for fn in ("ms-appliedstatsbrochure.pdf", "PhD_mathbrochure_051519.pdf",
               "NJIT DMS Math Major Flyer.pdf", "M107-ExamIB.pdf", "111ex1S22.pdf",
               "Analysis-Qual-submitted-S24.pdf", "placement.pdf", "CAMS_AR_2020.pdf"):
        assert cc.is_math_syllabus(base + fn) is False, fn


def test_is_math_syllabus_keeps_real_corpus_exam_filenames():
    # ADVERSARIAL — these are REAL "Math<num>…exam" filenames from the corpus that ^Math\d matched;
    # they are exams/finals the design KEEPS and MUST NOT be dropped (senior-eng rev e5228bc).
    base = "https://math.njit.edu/sites/math/files/"
    for fn in ("Math 107 Exam 1 Fall 2022.pdf", "Math 107 Final Exam Fall 2022.pdf",
               "Math_222_Exam_1_F17.pdf", "Math_222_FinalExam_F17.pdf",
               "Math 213 Final, 2024 Spring.pdf", "math337-final-fall2018.pdf",
               "Math 110_Fall 2024_E1.pdf", "Math 110_Fall 2024_FE.pdf", "Math 333 Exam II.pdf"):
        assert cc.is_math_syllabus(base + fn) is False, fn


def test_is_math_syllabus_true_for_real_corpus_syllabus_filenames():
    # genuine course syllabi (incl. instructor-named + Winter-term) that MUST be skipped
    base = "https://math.njit.edu/sites/math/files/"
    for fn in ("Math_107-F18.pdf", "Math_105-004-012-S21.pdf", "Math 105 (Jean) SP23.pdf",
               "Math 108 W2021-22.pdf", "MATH 373 SP23.pdf", "Math 661-SU22 (Newark Online).pdf"):
        assert cc.is_math_syllabus(base + fn) is True, fn


def test_is_math_syllabus_false_off_host_or_non_pdf():
    assert cc.is_math_syllabus("https://cs.njit.edu/sites/math/files/Math_107-F18.pdf") is False
    assert cc.is_math_syllabus("https://math.njit.edu/sites/math/files/Math_107-F18.html") is False
    assert cc.is_math_syllabus("https://math.njit.edu/academics/Math_107.pdf") is False  # not /files/


def test_is_math_syllabus_parses_host_path_not_substring():
    # a NON-math URL that merely CONTAINS the dir substring (in a query/path) must NOT be excused
    assert cc.is_math_syllabus(
        "https://cs.njit.edu/redirect?u=math.njit.edu/sites/math/files/Math_1-F18.pdf") is False
    assert cc.is_math_syllabus(
        "https://evil.example.com/math.njit.edu/sites/math/files/Math_1-F18.pdf") is False


# ────────────────────────── SECTION_ENTRY_POINTS registry ──────────────────────────

def test_section_entry_points_are_declared_static_and_scoped():
    S = cc.SECTION_ENTRY_POINTS
    assert len(S) >= 26                                   # ist + 24 offices + njitresearch + stem
    hosts = {urlparse(e.seed).netloc.lower() for e in S}
    assert hosts <= {"www.njit.edu", "ist.njit.edu"}      # njit hosts only — no external pollution
    # every www section seed is PATH-scoped (a subtree, never the bare homepage that DFSes the world)
    for e in S:
        p = urlparse(e.seed)
        if p.netloc.lower() == "www.njit.edu":
            assert p.path.strip("/"), f"bare www seed would crawl the whole site: {e.seed}"
    # the derived office orgs are present
    slugs = {e.org_slug for e in S}
    assert {"registrar", "financialaid", "eos", "ogi", "bursar", "career-development"} <= slugs


# ────────────────────────── PDF ingest skips syllabi (before fetch) ──────────────────────────

def _mkdb(tmp_path, name="t.db"):
    db = str(tmp_path / name)
    create_all(db)
    return get_connection(db)


def test_ingest_pdf_pages_skips_math_syllabus_without_fetching(tmp_path):
    conn = _mkdb(tmp_path)
    base = "https://math.njit.edu/sites/math/files/"
    syllabus = base + "Math_107-F18.pdf"
    brochure = base + "ms-appliedstatsbrochure.pdf"
    calls = []

    def fetch_bytes(u):
        calls.append(u)
        return None                                        # brochure → fetch_failed (fine for this test)

    out = cc.ingest_pdf_pages(conn, "mathematical-sciences", "Mathematical Sciences", "csla",
                              [(syllabus, "syl"), (brochure, "bro")], fetch_bytes,
                              org_type="department")
    assert syllabus not in calls                            # skipped BEFORE any network fetch
    assert brochure in calls
    reasons = {s["url"]: s.get("reason") for s in out["skipped"]}
    assert reasons.get(syllabus) == "math_syllabus"
    assert out["pdf_inserted"] == 0                         # syllabus not inserted; brochure fetch_failed


# ────────────────────────── coverage gate: math syllabi are an accepted drop ──────────────────────────

def test_coverage_gate_drop_pred_excludes_math_syllabi(tmp_path):
    from scripts.prose_rebuild_gate import coverage_gate

    def seed(name, urls):
        conn = _mkdb(tmp_path, name)
        conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
        for u in urls:
            conn.execute(
                "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                "version,is_active,created_by) VALUES(1,'pdf','t','body long enough',"
                "json_object('natural_key',?),?,1,1,'college_crawl')", (u, u))
        conn.commit()
        return conn

    syl = "https://math.njit.edu/sites/math/files/Math_107-F18.pdf"
    kept = "https://cs.njit.edu/about"
    backup = seed("b.db", [syl, kept])
    rebuilt = seed("r.db", [kept])                          # syllabus intentionally gone

    res = coverage_gate(rebuilt, backup, drop_pred=cc.is_math_syllabus)
    assert syl not in res["missing_urls"]                   # dropped, not a failure
    assert res["ok"] is True


# ────────────────────────── DFS supplement assembles seeds + writes canonical ──────────────────────────

def test_dfs_supplement_crawls_entry_and_dedups(tmp_path):
    from scripts.rebuild_prose import dfs_supplement

    conn = _mkdb(tmp_path)
    seed = "https://cs.njit.edu/"
    page = ("<html><head><title>Grad Advising</title></head><body>"
            + ("Graduate advising details for CS students. " * 20)
            + '<a href="/grad/advising">more</a></body></html>')
    sub = ("<html><head><title>Advising Detail</title></head><body>"
           + ("Detailed advising subpage content here. " * 20) + "</body></html>")
    pages = {seed: page, "https://cs.njit.edu/grad/advising": sub}

    def fetch(u):
        return pages.get(u.rstrip("/") if u != seed else u) or pages.get(u)

    entry = cc.ProseEntry(seed, "computer-science", "Computer Science", "ywcc", "department")
    out = dfs_supplement(conn, fetch, lambda u: None, entries=[entry], budget=10, delay=0.0)
    assert out["prose_inserted"] >= 1                       # DFS found + wrote the subtree page(s)

    # a second run over the same entry is idempotent (canonical index → unchanged, not duplicated)
    out2 = dfs_supplement(conn, fetch, lambda u: None, entries=[entry], budget=10, delay=0.0)
    assert out2["prose_inserted"] == 0


def test_dfs_supplement_isolates_a_failing_entry(tmp_path):
    """One entry that raises must NOT abort the whole rebuild — it is recorded and the rest proceed."""
    from scripts.rebuild_prose import dfs_supplement

    conn = _mkdb(tmp_path)
    good_seed = "https://cs.njit.edu/"
    good_html = ("<html><head><title>Good</title></head><body>"
                 + ("Real prose content for the good entry. " * 20) + "</body></html>")

    def fetch(u):
        if u.startswith("https://boom.njit.edu"):
            raise RuntimeError("simulated fetch/parse blow-up")
        return good_html if u.rstrip("/") == good_seed.rstrip("/") else None

    bad = cc.ProseEntry("https://boom.njit.edu/", "njit", "NJIT", "njit", "office")
    good = cc.ProseEntry(good_seed, "computer-science", "Computer Science", "ywcc", "department")
    out = dfs_supplement(conn, fetch, lambda u: None, entries=[bad, good], budget=5, delay=0.0)
    assert "boom.njit.edu" in " ".join(out["failed_entries"])   # the bad seed is recorded, not swallowed
    assert out["prose_inserted"] >= 1                            # the good entry still ran
