"""parse_entity over the people.njit.edu template — uncapped, +service/about."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.njit_adapter import (
    entity_id_from_url,
    is_valid_profile,
    parse_entity,
)

# A trimmed but structurally faithful copy of the NJIT profile template: a
# div.tabbed-content with about/teaching/research/publications/service panes, and
# the publications pane wrapping individual citation divs in a container div (the
# shape that used to cause 40 KB bloat). Includes a "SHOW MORE" noise leaf-div and
# a bare "Journal Article" type label that must NOT be captured as a publication.
HTML = """
<html><head><title>Ioannis Koutis | NJIT</title></head><body>
  <div class="position">Associate Professor<br>Associate Chair of Graduate Studies</div>
  <div class="department">Department of Computer Science</div>
  <span class="phone1">973-596-1234</span>
  ikoutis@njit.edu 4308 GITC Center
  <div class="tabbed-content">
    <a class="tab-control" data-target="about">About</a>
    <a class="tab-control" data-target="teaching">Teaching</a>
    <a class="tab-control" data-target="research">Research</a>
    <a class="tab-control" data-target="publications">Publications</a>
    <a class="tab-control" data-target="service">Service</a>
    <div class="tab-content">
        <div>About Me</div>
        <div>Koutis works on spectral graph theory.</div>
        <div>Education</div>
        <div>Ph.D.; Carnegie Mellon University; 2007 B.S.; University of Crete; 2002</div>
        <div>Awards &amp; Honors</div>
        <div>2012 NSF CAREER award, National Science Foundation 2017 ICALP Best Paper Award, EATCS</div>
        <div>Experience</div>
        <div>Associate Professor, June 2018 -</div>
        <div>Website</div>
        <div>https://koutis.example.edu</div>
        <a href="https://scholar.google.com/citations?user=abc">Scholar</a></div>
    <div class="tab-content"><div>CS 610 Data Structures and Algorithms</div>
        <div>CS 786 Advanced Algorithms</div></div>
    <div class="tab-content"><div>Research Interests Spectral graph theory; Fast Laplacian solvers;
        Graph sparsification.</div></div>
    <div class="tab-content">
      <div class="container">
        <div>SHOW MORE</div>
        <div>Journal Article
            Ioannis Koutis, Ryan Williams. 2016. "Limits and Applications of Group
            Algebras for Parameterized Problems." ACM Trans. Algorithms 12 (3).<br/><br/>
            Ioannis Koutis, Gary Miller. 2011. "A nearly-m log n time solver for SDD
            linear systems." FOCS 2011.<br/><br/>
            D. Spielman, I. Koutis. 2014. "Spectral sparsification of graphs." SIAM
            J. Comput. 43 (4).<br/><br/></div>
      </div>
    </div>
    <div class="tab-content"><div>Program Committee, SODA 2020</div>
        <div>Editorial Board, Journal of Graph Algorithms</div></div>
  </div>
</body></html>
"""

URL = "https://people.njit.edu/profile/ikoutis"


def rec():
    return parse_entity(URL, HTML)


def test_entity_id_from_url():
    assert entity_id_from_url(URL) == "people.njit.edu/profile/ikoutis"
    assert entity_id_from_url("https://people.njit.edu/profile/jdoe/") == \
        "people.njit.edu/profile/jdoe"


def test_entity_id_is_case_insensitive():
    # a differently-cased URL must reconcile to the SAME entity, not a duplicate
    assert entity_id_from_url("https://people.njit.edu/profile/IKoutis") == \
        entity_id_from_url("https://people.njit.edu/profile/ikoutis")


def test_newline_separated_citations_still_split():
    # defensive: a leaf-div whose citations are newline-separated (no <br>) must
    # NOT collapse into one blob
    html = """<html><body><div class="tabbed-content">
        <a class="tab-control" data-target="publications">Pubs</a>
        <div class="tab-content"><div>
          Alice Smith, Bob Lee. 2020. "Scalable graph clustering at web scale."
          Proceedings of the Web Conference, vol. 11, pp. 100-120.
          Carol Jones, Dan Park. 2019. "Fast approximate nearest neighbors in
          high dimensions." Journal of Machine Learning Research, vol. 20.
        </div></div></div></body></html>"""
    pubs = parse_entity("https://people.njit.edu/profile/x", html).publications
    assert len(pubs) == 2
    assert {p.year for p in pubs} == {"2020", "2019"}
    assert "graph clustering" in pubs[0].title and "nearest neighbors" in pubs[1].title


def test_multiline_citation_title_and_venue_stay_one_paper():
    # NJIT separates papers by DOUBLE <br>; a SINGLE <br> is an internal line break
    # (title line / venue+date line). Splitting on every <br> would drop the title
    # (no year) and keep a titleless venue fragment. Must yield ONE complete paper.
    html = """<html><body><div class="tabbed-content">
        <a class="tab-control" data-target="publications">Pubs</a>
        <div class="tab-content"><div>
          "UCS: Ultimate Course Search"<br/>
          14th International Workshop on Content-Based Multimedia Indexing, June, 2016.<br/><br/>
          "Flexible Aggregate Similarity Search"<br/>
          International Conference on Similarity Search, October, 2015.<br/><br/>
        </div></div></div></body></html>"""
    pubs = parse_entity("https://people.njit.edu/profile/x", html).publications
    assert len(pubs) == 2                                   # two papers, not four fragments
    assert all('"' in p.title for p in pubs)                # title retained, no orphan venue
    assert "Ultimate Course Search" in pubs[0].title and "2016" in pubs[0].title
    assert {p.year for p in pubs} == {"2016", "2015"}


def test_no_tabbed_content_degrades_gracefully():
    html = "<html><head><title>Jane Doe | NJIT</title></head><body>stub</body></html>"
    r = parse_entity("https://people.njit.edu/profile/jdoe", html)
    assert r.name == "Jane Doe"
    assert r.publications == [] and r.teaching == [] and r.service == []


def test_basic_identity_and_titles():
    r = rec()
    assert r.name == "Ioannis Koutis"
    assert r.titles == ["Associate Professor", "Associate Chair of Graduate Studies"]
    assert r.org == "Computer Science"
    assert r.source_url == URL
    assert r.verified is True


def test_contact_parsed():
    # office parsing is best-effort (NJIT address strings vary); email/phone are firm
    c = rec().contact
    assert c["email"] == "ikoutis@njit.edu"
    assert c["phone"] == "973-596-1234"


def test_all_real_publications_captured_no_noise_no_cap():
    pubs = rec().publications
    # exactly the 3 real citations — NOT "Journal Article" or "SHOW MORE"
    assert len(pubs) == 3
    titles = " | ".join(p.title for p in pubs)
    assert "Journal Article" not in titles and "SHOW MORE" not in titles
    assert {p.year for p in pubs} == {"2016", "2011", "2014"}
    # full citation text is kept (not truncated)
    assert "ACM Trans. Algorithms" in pubs[0].title


def test_research_teaching_service_education_bio():
    r = rec()
    assert "Laplacian" in r.research_statement
    assert any("Data Structures" in t for t in r.teaching)
    assert any("Program Committee" in s for s in r.service)
    assert r.bio == "Koutis works on spectral graph theory."   # only About Me, not the rest
    assert any("Carnegie Mellon" in e for e in r.education)
    # education no longer absorbs Awards/Website (they are their own sections)
    assert not any("NSF" in e or "http" in e for e in r.education)


def test_about_sections_split_awards_experience_website():
    r = rec()
    # Awards & Honors -> one item per award (was lumped into education)
    assert any("NSF CAREER" in a for a in r.awards)
    assert any("ICALP Best Paper" in a for a in r.awards)
    assert len(r.awards) == 2
    # Experience section captured (the rank/date line)
    assert any("Associate Professor, June 2018" in e for e in r.experience)


def test_research_interests_become_areas():
    r = rec()
    assert "Spectral graph theory" in r.research_areas
    # trailing period is stripped — it is not part of the area name
    assert "Graph sparsification" in r.research_areas
    assert "Graph sparsification." not in r.research_areas
    assert len(r.research_areas) == 3


# ── research_areas extraction is structural, not greedy (the 2026-06-14 fix) ────

def _areas(research_pane_html: str) -> list[str]:
    """Parse a profile whose research pane is `research_pane_html`, return areas."""
    html = ('<html><body><div class="tabbed-content">'
            '<a class="tab-control" data-target="research">Research</a>'
            f'<div class="tab-content">{research_pane_html}</div>'
            '</div></body></html>')
    return parse_entity("https://people.njit.edu/profile/x", html).research_areas


def test_comma_list_in_own_div_excludes_grants():
    # the real people.njit.edu layout: label div, comma list div, then a grants
    # section ("In Progress" + project blurbs) that must NOT bleed into the areas.
    areas = _areas(
        "<div>Research Interests</div>"
        "<div>Data mining, machine learning, deep learning, generative AI, data science</div>"
        "<div>In Progress</div>"
        "<div>Mining Big Data Through Deep Learning, a five-year NSF project to ...</div>")
    assert areas == ["Data mining", "machine learning", "deep learning",
                     "generative AI", "data science"]


def test_label_then_grants_with_no_list_returns_empty():
    # label present but the next node is a grant-section label → no interests listed.
    areas = _areas("<div>Research Interests</div><div>In Progress</div>"
                   "<div>Some funded project description.</div>")
    assert areas == []


def test_compound_and_period_are_cleaned():
    areas = _areas("<div>Research Interests Algorithms, Operations Research, "
                   "and Artificial Intelligence.</div>")
    assert areas == ["Algorithms", "Operations Research", "Artificial Intelligence"]


def test_areas_are_deduped_case_insensitively():
    areas = _areas("<div>Research Interests Machine learning, machine learning, "
                   "Deep Learning</div>")
    assert areas == ["Machine learning", "Deep Learning"]


def test_parenthetical_commas_do_not_split_the_area():
    # an illustrative comma/semicolon list inside parens is part of ONE area and must
    # not fragment on its internal delimiters (the 2026-06-14 paren-aware split).
    areas = _areas(
        "<div>Research Interests</div>"
        "<div>Video Analytics, Machine Learning (Statistical Learning, Kernel Methods, "
        "Similarity Measures), Computer Vision</div>")
    assert areas == [
        "Video Analytics",
        "Machine Learning (Statistical Learning, Kernel Methods, Similarity Measures)",
        "Computer Vision"]


def test_no_research_interests_label_returns_empty():
    assert _areas("<div>Some research blurb with no interests label.</div>") == []


def test_list_with_glued_prose_tail_keeps_only_the_list():
    # the real people.njit.edu shape: a comma list with a research-statement / recruiting
    # blurb glued on with no delimiter — the prose tail must be cut at the first-person turn.
    areas = _areas("<div>Research Interests</div><div>Data mining, machine learning, "
                   "data science I am seeking students to join my NSF projects.</div>")
    assert areas == ["Data mining", "machine learning", "data science"]


def test_pure_prose_statement_returns_empty():
    # some profiles write only a prose statement (headed paragraphs), no list → honest empty
    # (the prose still lives in research_statement for semantic search).
    areas = _areas("<div>Research Interests</div><div>Neurosymbolic AI &amp; Explainable "
                   "Systems: Integrating symbolic structure with deep learning for "
                   "transparent reasoning. We develop hybrid architectures.</div>")
    assert areas == []


def test_verbose_semicolon_areas_kept():
    areas = _areas("<div>Research Interests</div><div>Algorithm design; Spectral graph "
                   "theory; Applications in Graph Machine Learning and Deep Learning.</div>")
    assert areas == ["Algorithm design", "Spectral graph theory",
                     "Applications in Graph Machine Learning and Deep Learning"]


def test_repeated_leadin_boilerplate_stripped():
    # some authors repeat "His/My research interests are/include" inside the content
    areas = _areas("<div>Research Interests</div><div>His research interests are geometric "
                   "design, computer graphics, machine learning</div>")
    assert areas == ["geometric design", "computer graphics", "machine learning"]


def test_single_token_dropped_for_precision():
    # one token from this free-text field is empirically always prose, never a real list
    areas = _areas("<div>Research Interests</div>"
                   "<div>Keeping updated for the new technology trends</div>")
    assert areas == []


def test_leading_dash_and_bullet_artifacts_stripped():
    areas = _areas("<div>Research Interests</div><div>- Relational, Object, NoSQL</div>")
    assert areas == ["Relational", "Object", "NoSQL"]


# ── skip-not-clobber: a structureless page is a failed fetch, not empty data ────

def test_is_valid_profile_requires_tabbed_content():
    # the real NJIT profile template always has div.tabbed-content
    assert is_valid_profile('<html><body><div class="tabbed-content">x</div></body></html>')
    # a JS shell / error / transient page has none → must NOT be treated as a real profile
    assert not is_valid_profile("<html><body>just a 57kb js shell, no panes</body></html>")
    assert not is_valid_profile("")
    assert not is_valid_profile(None)


def test_links_extracted():
    links = rec().links
    assert "scholar.google.com" in links.get("scholar", "")
    assert links.get("website") == "https://koutis.example.edu"   # from the Website section
