"""parse_entity over the people.njit.edu template — uncapped, +service/about."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.njit_adapter import entity_id_from_url, parse_entity

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
    <div class="tab-content"><div>About Me Koutis works on spectral graph theory.
        Education Ph.D. Carnegie Mellon University 2007 B.S. University of Crete 2002</div>
        <a href="https://scholar.google.com/citations?user=abc">Scholar</a>
        <a href="https://koutis.example.edu">Website</a></div>
    <div class="tab-content"><div>CS 610 Data Structures and Algorithms</div>
        <div>CS 786 Advanced Algorithms</div></div>
    <div class="tab-content"><div>Spectral graph theory and fast Laplacian solvers
        for large-scale graph problems.</div></div>
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
    assert "spectral graph theory" in r.bio.lower()
    assert any("Carnegie Mellon" in e for e in r.education)


def test_links_extracted():
    links = rec().links
    assert "scholar.google.com" in links.get("scholar", "")
    assert links.get("website") == "https://koutis.example.edu"
