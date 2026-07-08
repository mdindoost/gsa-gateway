import sys, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.run_pilot import _qkey, run_pilot


def test_qkey_stable_and_distinct():
    a = _qkey("who is x", "positive_control")
    assert a == _qkey("who is x", "positive_control")
    assert a != _qkey("who is x", "oracle_blind")          # stratum matters
    assert a != _qkey("who is y", "positive_control")       # question matters


def test_resume_skips_questions_already_done(tmp_path):
    # pre-mark the one pair as done; oracle MUST NOT be called for it
    done_file = tmp_path / "done_controls.txt"
    done_file.write_text(_qkey("done q", "positive_control") + "\n")
    def _boom_oracle(q):
        raise AssertionError("oracle called for an already-done question")
    out = run_pilot([("done q", "positive_control")], "controls",
                    conn=object(), out_dir=str(tmp_path),
                    oracle=_boom_oracle, runner=lambda q: "", resume=True)
    assert Path(out).name == "facts_controls.jsonl"        # returns the path, no crash, oracle never called


def test_resume_false_truncates_prior_output(tmp_path):
    (tmp_path / "facts_controls.jsonl").write_text('{"stale": true}\n')
    (tmp_path / "done_controls.txt").write_text("stalekey\n")
    # resume=False with an empty sample clears both prior files
    run_pilot([], "controls", conn=object(), out_dir=str(tmp_path),
              oracle=lambda q: None, runner=lambda q: "", resume=False)
    assert (tmp_path / "facts_controls.jsonl").read_text() == ""     # truncated
    assert (tmp_path / "done_controls.txt").read_text() == ""
