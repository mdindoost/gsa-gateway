from bot.core.headsup import match_topic, headsup_line, apply_headsup


def test_immigration_topics_match():
    for q in ["How do I apply for CPT?", "When do I get my I-20?",
              "How do I apply for OPT before graduation?", "questions about my visa"]:
        t = match_topic(q)
        assert t is not None and t.name == "immigration"


def test_billing_and_funding_match():
    assert match_topic("How do I pay my tuition?").name == "billing"
    assert match_topic("Why do I have a financial hold?").name == "billing"
    assert match_topic("How do I apply for a teaching assistant position?").name == "funding"
    assert match_topic("What is the stipend for a funded PhD student?").name == "funding"


def test_normal_gsa_questions_do_not_match():
    for q in ["Who are the GSA officers?", "What is the travel award?",
              "When is the next GSA event?", "What are the VP of Finance duties?"]:
        assert match_topic(q) is None


def test_headsup_line_names_the_office():
    t = match_topic("How do I apply for CPT?")
    assert "Office of Global Initiatives" in headsup_line(t)


def test_apply_headsup_appends_for_highstakes_only():
    out = apply_headsup("You apply for CPT via OGI.", "How do I apply for CPT?")
    assert out.startswith("You apply for CPT via OGI.")
    assert "confirm with" in out.lower()
    # normal question: unchanged
    same = apply_headsup("The travel award is $900.", "What is the max travel award?")
    assert same == "The travel award is $900."
