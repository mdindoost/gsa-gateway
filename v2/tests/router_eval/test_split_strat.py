from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.split import split, split_entity_disjoint, entity_of


def test_entity_of_extracts_org_person_area():
    assert entity_of(LabeledExample("1", "q", "KG", "faculty_in_department", slots={"org": "CS"})) == "org:cs"
    assert entity_of(LabeledExample("2", "q", "KG", "entity_card", slots={"person": "Ioannis Koutis"})) == "person:ioannis koutis"
    assert entity_of(LabeledExample("3", "q", "KG", "people_by_research_area", slots={"area": "graph"})) == "area:graph"
    assert entity_of(LabeledExample("4", "q", "RAG", source="general")) is None


def test_entity_disjoint_no_entity_on_both_sides():
    ex = []
    for i, org in enumerate(["cs", "math", "bio", "chem", "me", "ywcc"]):
        ex.append(LabeledExample(f"f{i}", f"who teaches in {org}", "KG", "faculty_in_department",
                                 slots={"org": org}, group=f"fac-{org}"))
        ex.append(LabeledExample(f"o{i}", f"officers of {org}", "KG", "officers_in_org",
                                 slots={"org": org}, group=f"off-{org}"))
    tr, te = split_entity_disjoint(ex, test_frac=0.34, seed=1)
    tr_ent = {entity_of(e) for e in tr}
    te_ent = {entity_of(e) for e in te}
    assert te_ent and tr_ent.isdisjoint(te_ent)        # held-out entities only in test


def test_entity_disjoint_sends_entityless_rows_to_train():
    ex = [LabeledExample("g1", "what is the gsa", "RAG", source="general", group="g1"),
          LabeledExample("k1", "who teaches in cs", "KG", "faculty_in_department",
                         slots={"org": "cs"}, group="kc")]
    tr, te = split_entity_disjoint(ex, test_frac=0.9, seed=0)
    assert any(e.id == "g1" for e in tr)               # entity-less RAG row stays in train
    assert all(entity_of(e) is not None for e in te)   # test holds only entity-bearing rows


def test_split_is_skill_stratified():
    # two KG skills; the rare skill has a single group -> must stay in train (testable-skill rule)
    ex = [LabeledExample(f"a{i}", f"faculty q{i}", "KG", "faculty_in_department", group=f"fa{i}")
          for i in range(6)]
    ex += [LabeledExample("rare", "the one areas query", "KG", "areas_in_org", group="rare")]
    tr, te = split(ex, FakeEncoder(16), test_frac=0.5, seed=3)
    assert any(e.skill == "areas_in_org" for e in tr)  # single-group skill kept in train
    assert all(e.skill != "areas_in_org" for e in te)  # never tested with no train exemplar
