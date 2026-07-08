"""Fable pronoun ruling: mechanically detect dangling-opener nuggets whose SUBJECT is an
unresolved anaphor (He/She/It/They/This/'The program') — these are unjudgeable in isolation,
so they're excluded from the kappa denominator + headline debt and reported as their own bucket.
Full nuggetizer pronoun-resolution stays deferred."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.self_contained import is_self_contained


def test_pronoun_opener_is_not_self_contained():
    assert is_self_contained("He joined the program in September 2022.") is False
    assert is_self_contained("His research interests include privacy.") is False
    assert is_self_contained("They received a travel award.") is False
    assert is_self_contained("It was founded in 1881.") is False

def test_demonstrative_opener_is_not_self_contained():
    assert is_self_contained("This program requires a bachelor's degree.") is False
    assert is_self_contained("These courses have no prerequisites.") is False

def test_the_plus_common_noun_is_not_self_contained():
    assert is_self_contained("The program is 30 credits.") is False
    assert is_self_contained("The department offers a PhD.") is False

def test_the_plus_proper_noun_is_self_contained():
    assert is_self_contained("The Grill in the Campus Center offers Halal choices.") is True
    assert is_self_contained("The NJIT Highlander Commons serves breakfast.") is True

def test_named_subject_is_self_contained():
    assert is_self_contained("Shantanu Sharma is an Assistant Professor.") is True
    assert is_self_contained("NJIT is located in Newark, New Jersey.") is True
    assert is_self_contained("Media Relations: Deric Raymond, 973-642-7042") is True

def test_requirement_phrase_is_self_contained():
    # a bare requirement is judgeable (subject = the requirement), 'a/an' openers are not anaphoric
    assert is_self_contained("At least one letter of recommendation") is True
    assert is_self_contained("A bachelor's degree is required.") is True

def test_empty_or_nontext_is_self_contained():
    assert is_self_contained("") is True
    assert is_self_contained("973-596-3000") is True
