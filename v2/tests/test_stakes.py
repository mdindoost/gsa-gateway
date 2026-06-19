"""Safety core for the NJIT grad-content crawl (task #5): stakes classification + volatile
redaction + the value tripwire. These are the guards that keep stale/high-stakes facts off
the student-facing path, so they're tested exhaustively."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion import stakes as S

URL_BURSAR = "https://njit.edu/bursar/for-students"
URL_INTL = "https://njit.edu/global/cpt"
URL_IST = "https://ist.njit.edu/wifi"


# ── volatile redaction (drop the value, point to live page) ──────────────────────
def test_money_line_redacted():
    txt = "Tuition is $1,234.50 per credit for graduate students."
    clean, n = S.redact_volatile(txt, URL_BURSAR)
    assert n == 1
    assert "$" not in clean and "1,234" not in clean
    assert URL_BURSAR in clean

def test_percent_line_redacted():
    clean, n = S.redact_volatile("Withdrawing in week 2 refunds 50% of tuition.", URL_BURSAR)
    assert n == 1 and "%" not in clean

def test_deadline_date_redacted_but_plain_date_kept():
    txt = ("The payment deadline is September 15.\n"
           "The Bursar's office opened in 1881.")
    clean, n = S.redact_volatile(txt, URL_BURSAR)
    assert n == 1
    assert "September 15" not in clean       # deadline date → redacted
    assert "1881" in clean                   # non-deadline number → kept

def test_stable_text_untouched():
    txt = "Pay online through the Highlander Pipeline portal under Student Accounts."
    clean, n = S.redact_volatile(txt, URL_BURSAR)
    assert n == 0 and clean == txt

def test_per_credit_rule_without_number_kept():
    # "billed per credit" is a stable rule, NOT a value — must not be redacted
    clean, n = S.redact_volatile("Graduate tuition is billed per credit hour.", URL_BURSAR)
    assert n == 0


# ── tripwire ──────────────────────────────────────────────────────────────────────
def test_tripwire_catches_surviving_money():
    assert S.has_unredacted_value("the fee is $200") is True

def test_tripwire_clean_after_redaction():
    clean, _ = S.redact_volatile("Tuition is $1,234 per credit.", URL_BURSAR)
    assert S.has_unredacted_value(clean) is False


# ── doc-level staging classification ──────────────────────────────────────────────
def test_immigration_rule_is_high():
    assert S.classify_doc(URL_INTL, "F-1 students must maintain status and may apply for CPT.") == "high"

def test_forfeiture_rule_is_high():
    assert S.classify_doc(URL_BURSAR, "Withdrawing after week 3 forfeits all tuition.") == "high"

def test_financial_tree_with_values_is_high():
    # a tuition-schedule doc (had $ before redaction) → stage even though the value is gone
    assert S.classify_doc(URL_BURSAR, "Tuition is (see live page).", had_volatile=True) == "high"

def test_bursar_hours_no_rule_no_value_is_low():
    assert S.classify_doc(URL_BURSAR, "The Bursar is open weekdays 9-5 in Fenster Hall.",
                          had_volatile=False) == "low"

def test_low_tree_procedure_is_low():
    assert S.classify_doc(URL_IST, "Connect to the NJIT secure Wi-Fi with your UCID.") == "low"

def test_unknown_in_high_tree_with_values_defaults_high():
    assert S.classify_doc("https://njit.edu/financialaid/x", "ambiguous text",
                          had_volatile=True) == "high"
