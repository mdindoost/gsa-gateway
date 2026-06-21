from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.classifier import ExemplarClassifier

EX = [
    LabeledExample("1", "who teaches in computer science", "KG", "faculty_in_department"),
    LabeledExample("2", "list the faculty in math", "KG", "faculty_in_department"),
    LabeledExample("3", "free food on campus today", "RAG", source="food"),
    LabeledExample("4", "any free pizza this week", "RAG", source="food"),
]


def test_family_level_routes_to_nearest():
    enc = FakeEncoder(16)
    clf = ExemplarClassifier(level="family"); clf.fit(EX, enc)
    label, score, margin = clf.top("who teaches in biology", enc)
    assert label == "KG" and 0.0 <= score <= 1.0 and margin >= 0.0


def test_skill_level_label_format():
    enc = FakeEncoder(16)
    clf = ExemplarClassifier(level="skill"); clf.fit(EX, enc)
    label, _, _ = clf.top("list the faculty in chemistry", enc)
    assert label == "KG/faculty_in_department"
