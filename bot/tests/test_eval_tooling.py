"""TDD — eval-suite correctness fixes (Wave 2, Fable audit 2026-07-04).

Covers the three drift bugs that corrupted eval numbers:
  C3 — live answers were miscounted as KB (stale text prefix) → classify off the is_live signal.
  C4 — abstentions were miscounted as answered (text escaped the deflect check) → shared markers.
  H1 — the judge scored "INCORRECT" as correct (substring) → word-boundary, ordered checks.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import importlib.util


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / "scripts" / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


eval_run = _load("eval_run", "eval_run.py")
eval_judge = _load("eval_judge", "eval_judge.py")

# the real live prefix + the real abstain templates (imported, not hand-copied)
from bot.core.live_query import LIVE_NOT_FOUND_MSG
from bot.core.message_handler import _KB_MISS_RESPONSE

_LIVE_PREFIX = "🌐 Live from NJIT's website (fetched live): "
_USEFUL_ABSTAIN = (
    "I wasn't able to find a specific answer to that in the GSA knowledge base.\n\n"
    "For accurate information, please:\n- Visit the GSA office ...")
_RAG_ERROR = "I encountered an error processing your question. Please try again."


# ═══════════════ C3 — live answers classify as 'live' ═══════════════
def test_c3_live_answer_classifies_live_via_flag():
    assert eval_run.classify(_LIVE_PREFIX + "the library is open 24h.", is_live=True) == "live"


def test_c3_live_flag_wins_even_if_text_looks_like_kb():
    # is_live is authoritative — never fall back to a text prefix that has already drifted once.
    assert eval_run.classify("some answer text with no special prefix", is_live=True) == "live"


# ═══════════════ C4 — abstain classified off the is_abstain FLAG, not answer text ═══════════════
def test_c4_is_abstain_flag_is_deflect_regardless_of_text():
    # every canned non-answer carries is_abstain=True at source → deflect, whatever the wording
    for txt in (_KB_MISS_RESPONSE, _USEFUL_ABSTAIN, LIVE_NOT_FOUND_MSG, _RAG_ERROR):
        assert eval_run.classify(txt, is_live=False, is_abstain=True) == "deflect"


def test_c4_no_text_coupling_kb_miss_without_flag_is_kb():
    # the whole point of the rewire: a canned text WITHOUT the flag is not special-cased anymore
    assert eval_run.classify(_KB_MISS_RESPONSE, is_live=False, is_abstain=False) == "kb"


def test_c4_empty_is_deflect():
    assert eval_run.classify("", is_live=False) == "deflect"


def test_c4_real_kb_answer_classifies_kb():
    assert eval_run.classify("The GSA President convenes the Executive Board.", is_live=False) == "kb"


def test_c4_is_live_wins_over_is_abstain():
    assert eval_run.classify("anything", is_live=True, is_abstain=True) == "live"


# ═══════════════ H1 — judge grade parsing (word-boundary, ordered) ═══════════════
def test_h1_incorrect_is_wrong_not_correct():
    assert eval_judge.grade_reply("INCORRECT") == "wrong"


def test_h1_partially_correct_is_partial():
    assert eval_judge.grade_reply("PARTIALLY CORRECT") == "partial"


def test_h1_plain_correct_is_correct():
    assert eval_judge.grade_reply("CORRECT") == "correct"


def test_h1_wrong_is_wrong():
    assert eval_judge.grade_reply("WRONG") == "wrong"


def test_h1_garbage_defaults_wrong():
    assert eval_judge.grade_reply("hmm not sure") == "wrong"


def test_h1_not_correct_is_wrong():
    assert eval_judge.grade_reply("NOT CORRECT") == "wrong"


def test_h1_lowercase_and_sentence():
    assert eval_judge.grade_reply("The answer is correct and useful.") == "correct"
    assert eval_judge.grade_reply("This is incorrect.") == "wrong"


# ═══════════════ C5 — cleanup deletes ONLY eval rows, by hash (never a real user) ═══════════════
def test_c5_cleanup_removes_eval_rows_only(tmp_path):
    from bot.services.database import Database
    db = Database(str(tmp_path / "t.db"))
    db.connect()
    db.init_tables()
    # a REAL student's question, then three synthetic eval rows (as eval_run creates them)
    real_id = db.log_question("student-9999", "when is graduation?", None, None, None, "telegram")
    for i in range(3):
        db.log_question(f"eval-{i}", f"eval q {i}", None, None, None, "telegram")
    assert db.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 4

    deleted = eval_run.cleanup_eval_rows(db, n=3)
    assert deleted == 3
    rows = db.conn.execute("SELECT id FROM questions").fetchall()
    assert [r[0] for r in rows] == [real_id]          # the real student's row survives
    db.close()


def test_c5_cleanup_generous_range_is_safe_when_no_eval_rows(tmp_path):
    from bot.services.database import Database
    db = Database(str(tmp_path / "t2.db"))
    db.connect()
    db.init_tables()
    db.log_question("student-1", "hi", None, None, None, "telegram")
    assert eval_run.cleanup_eval_rows(db, n=2000) == 0      # nothing to delete, real row untouched
    assert db.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
    db.close()
