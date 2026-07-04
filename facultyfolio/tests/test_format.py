from facultyfolio import format as F


def test_normalize_name():
    assert F.normalize_name("Koutis, Ioannis") == "Ioannis Koutis"
    assert F.normalize_name("Oria, Vincent") == "Vincent Oria"
    assert F.normalize_name("Kieran Murphy") == "Kieran Murphy"
    assert F.normalize_name("Smith, John, Jr") == "Smith, John, Jr"  # multi-comma untouched
    assert F.initials("Ioannis Koutis") == "IK"
    assert F.initials("James Calvin") == "JC"


def test_smart_titlecase():
    assert F.smart_titlecase("EXPLAINABLE AI") == "Explainable AI"       # acronym preserved
    assert F.smart_titlecase("INTRO TO MACHINE LEARNING-HONORS").startswith("Intro To Machine")
    # truncated registrar words stay caps (accepted honest-but-ugly limitation)
    assert F.smart_titlecase("ADV DATA STRUCT-ALG DES") == "ADV Data Struct-Alg DES"


def test_clean_mojibake():
    assert "�" not in F.clean_mojibake("IEEE Symposium on�…, 2010")


def test_format_venue():
    raw1 = "Foundations of Computer Science (FOCS), 2010 51st Annual IEEE Symposium on�…, 2010"
    assert F.format_venue(raw1) == "FOCS 2010"
    raw2 = "arXiv preprint arXiv:2604.20078, 2026"
    assert F.format_venue(raw2) == "arXiv 2026"
    # no-acronym branch: honest fragment + year, NOT "FOCS 2011"
    raw3 = "Proceedings of the 2011 IEEE 52st Annual Symposium on Foundations of�…, 2011"
    v3 = F.format_venue(raw3)
    assert v3.endswith("2011") and "FOCS" not in v3


def test_format_teaching():
    raw = ("Past Courses; CS 375: INTRO TO MACHINE LEARNING-HONORS CS 435: ADV DATA STRUCT-ALG DES "
           "CS 610: DATA STRUCTURE & ALG CS 610: DATA STRUCTURES AND ALGORITHMS CS 611: COMPUTABILITY "
           "& COMPLEX CS 675: MACHINE LEARNING CS 677: DEEP LEARNING DS 675: MACHINE LEARNING "
           "DS 677: DEEP LEARNING")
    out = F.format_teaching(raw)
    assert "Machine Learning (CS 675 / DS 675)" in out          # cross-list grouped by title, codes shown
    # two CS 610 title-variants collapse to ONE entry (the longest title); single code -> no paren
    assert [e for e in out if e.startswith("Data Structures")] == ["Data Structures And Algorithms"]
    assert "Deep Learning (CS 677 / DS 677)" in out
    assert all("ST:" not in e for e in out)
    assert all("Past Courses" not in e for e in out)


def test_format_teaching_special_topics():
    raw = "Past Courses; CS 485: ST: EXPLAINABLE AI CS 698: ST:EXPLAINABLE AI CS 785: ST: EXPLAINABLE AI"
    assert F.format_teaching(raw) == ["Explainable AI (CS 485 / CS 698 / CS 785)"]


def test_format_education_4field():
    raw = ("Education of Ioannis Koutis (Computer Science): Ph.D.; Carnegie Mellon University; "
           "Computer Science; 2007; Diploma; University of Patras; Computer Engineering and Informatics; 1998")
    out = F.format_education(raw)
    assert out[0] == "Ph.D. Computer Science, Carnegie Mellon University (2007)"
    assert out[1] == "Diploma Computer Engineering and Informatics, University of Patras (1998)"
    assert len(out) == 2


def test_format_education_3field():   # B5 — variable-length record (no field component)
    raw = ("Education of James Calvin (Computer Science): Ph.D.; Stanford University; 1990; "
           "M.S.; University of California-Berkeley; 1979; B.A.; University of California-Berkeley; 1978")
    out = F.format_education(raw)
    assert out[0] == "Ph.D., Stanford University (1990)"     # no field segment
    assert out[1] == "M.S., University of California-Berkeley (1979)"
    assert len(out) == 3


def test_format_education_degree_only_omitted():
    assert F.format_education("Education of Vincent Oria (Computer Science): Ph.D.") == []


def test_format_office():
    assert F.format_office("4105 Guttenberg Information Technologies Center (GITC)") == "4105 GITC"
    assert F.format_office("") == ""


def test_commafy():
    assert F.commafy(2791) == "2,791"
    assert F.commafy(482) == "482"
