"""Tests for the deflection detector (bot/core/deflection.py).

This is the OFFER-only signal: a true positive surfaces a "want me to search NJIT?"
button, never an auto-fire. So we tune for recall on the shapes we actually emit, BUT the
negatives matter just as much — a false positive slaps an offer on a correct answer
(reframes it as suspect + spends a Brave credit). Honest-partial / heads-up answers that
route the user to an office are the project's CORRECT behavior and must NOT draw an offer.
"""
from bot.core.deflection import looks_like_deflection


# ── Positives: the answer punts the user elsewhere FOR the answer ──────────────
def test_volatile_info_pointed_to_njit_page_is_deflection():
    assert looks_like_deflection(
        "The library has study spaces. For current hours, see library.njit.edu."
    )


def test_see_njit_edu_target_is_deflection():
    assert looks_like_deflection(
        "You can find that on the registrar's page — please see registrar.njit.edu."
    )


def test_recommend_checking_the_site_for_latest_is_deflection():
    assert looks_like_deflection(
        "I'd recommend checking the department website for the latest deadlines."
    )


def test_explicit_no_info_admission_is_deflection():
    assert looks_like_deflection("I don't have that information in the knowledge base.")
    assert looks_like_deflection("I wasn't able to find specific details about that.")


# ── Negatives: correct answers that must NOT draw an offer ─────────────────────
def test_heads_up_contact_office_is_not_deflection():
    # honest-partial / heads-up routing to an office is CORRECT behavior, not a deflection
    assert not looks_like_deflection(
        "CPT is authorized by OGI. Please confirm with the Office of Global Initiatives."
    )
    assert not looks_like_deflection(
        "For your specific case, reach out to the Office of the Bursar."
    )


def test_faculty_bio_see_his_website_is_not_deflection():
    assert not looks_like_deflection(
        "Dr. Koutis researches graph algorithms; see his website for publications."
    )


def test_plain_factual_answer_is_not_deflection():
    assert not looks_like_deflection(
        "The maximum GSA travel award is $900 per academic year."
    )
    assert not looks_like_deflection(
        "The GSA office is in Campus Center 110A, open weekdays 11AM-5PM."
    )


def test_empty_text_is_not_deflection():
    assert not looks_like_deflection("")
