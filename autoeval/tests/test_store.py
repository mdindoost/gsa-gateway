import tempfile, os
from autoeval.store import Store

def _store():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    s = Store(path); s.init_schema(); return s

def test_run_question_result_roundtrip():
    s = _store()
    run_id = s.create_run(db_snapshot_hash="abc", config_json="{}",
                          codex_model="gpt-5-codex", kavosh_commit="deadbeef", live_enabled=False)
    q_id = s.insert_question(run_id, item_type="person", item_key="crawler/x", arm="answer",
                             variant_type=None, twin_ref=None, question_text="q?",
                             expected_json='{"type":"contact"}', codex_raw_ref="raw/1")
    s.insert_result(q_id, answer_text="a", metadata_json="{}", result="pass",
                    failure_class=None, data_gap=False, evidence_json="{}", latency_ms=12,
                    resolved_entity_id="crawler/x", family="KG", skill="contact_of_person",
                    used_ai=False, graded_soft=False, llm_judge_verdict=None,
                    llm_judge_confidence=None)
    rows = s.results_for_run(run_id)
    assert len(rows) == 1 and rows[0]["result"] == "pass" and rows[0]["arm"] == "answer"

def test_coverage_bump_and_least_tested():
    s = _store()
    s.bump_coverage("k1"); s.bump_coverage("k1"); s.bump_coverage("k2")
    least = s.least_tested_keys(limit=1)
    assert least == ["k2"]  # k2 tested once, k1 twice
