# autoeval/harness.py
from __future__ import annotations
import argparse, asyncio, json, subprocess
from pathlib import Path
from autoeval.config import load_config, assert_env
from autoeval.snapshot import make_snapshot, ro_connect
from autoeval.store import Store
from autoeval.sampler import sample_items
from autoeval.generator import generate
from autoeval.runner import KavoshRunner
from autoeval.checker import classify
from autoeval.judge import judge
from autoeval.report import build_report
from autoeval.resilience import write_status, sleep_until_reset
from autoeval.codex_client import RateLimitError

MAX_RESUME_CYCLES = 16  # safety cap on consecutive Codex usage-window waits for one item

def _kavosh_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"

async def _default_wait(reason: str) -> None:
    # Off-load the blocking sleep to a thread so the event loop stays responsive
    # (SIGTERM / cancellation) during a multi-hour Codex usage window.
    await asyncio.to_thread(sleep_until_reset, reason)

async def generate_with_resume(item, cfg, run_id, completed, total, *,
                               generate_fn=generate, wait_fn=_default_wait,
                               max_cycles: int = MAX_RESUME_CYCLES):
    """Generate questions for one item, auto-resuming across as many Codex usage-windows as
    needed (not a single retry). Each RateLimitError: mark paused (carry Codex's own reset
    reason), wait until the window reopens, mark running, retry. Raises the last RateLimitError
    only if max_cycles consecutive windows all throttle (safety cap)."""
    last = None
    for _ in range(max_cycles):
        try:
            return await generate_fn(item)
        except RateLimitError as e:
            last = e
            write_status(cfg.status_file, "paused", run_id=run_id, reason=str(e),
                         completed=completed, total=total)
            await wait_fn(str(e))
            write_status(cfg.status_file, "running", run_id=run_id, completed=completed, total=total)
    raise last

def _error_result(store, q_id, err):
    store.insert_result(
        q_id, answer_text="", metadata_json=json.dumps({"error": repr(err)}),
        result="error", failure_class=None, data_gap=False,
        evidence_json=json.dumps({"error": repr(err)}), latency_ms=0,
        resolved_entity_id=None, family=None, skill=None, used_ai=False,
        graded_soft=False, llm_judge_verdict=None, llm_judge_confidence=None)

async def run_window(cfg, n_items: int, smoke: bool = False):
    assert_env()
    store = Store(cfg.autoeval_db); store.init_schema()
    snap, snap_hash = make_snapshot(cfg.prod_db, cfg.snapshot_dir)
    gt_conn = ro_connect(snap)
    run_id = store.create_run(db_snapshot_hash=snap_hash, config_json=json.dumps(cfg.__dict__, default=str),
                              codex_model=cfg.codex_model or "default", kavosh_commit=_kavosh_commit(),
                              live_enabled=cfg.live_enabled)
    write_status(cfg.status_file, "running", run_id=run_id, completed=0, total=0)

    prefer = store.least_tested_keys(limit=n_items * 3)
    items = sample_items(gt_conn, cfg.sampler_mix, n_items, prefer_keys=prefer, seed=None if not smoke else 1)

    runner = KavoshRunner(cfg); await runner.build(snap)
    warmed = await runner.warm()   # pre-load Ollama so cold-start slot-extraction timeouts don't bias results
    print(f"[autoeval] model warm-up {'ok' if warmed else 'skipped/failed'}", flush=True)
    # arm-A pass tracking for A/B pairing, keyed by (item_key, twin question text)
    twin_pass: dict[tuple[str, str], bool] = {}
    completed = 0
    errored = None
    try:
        for item in items:
            questions = await generate_with_resume(item, cfg, run_id, completed, len(items))
            # order: answer arm first so twins are known before noisy arm
            questions.sort(key=lambda q: {"answer": 0, "noisy": 1, "out_of_scope": 2}.get(q.arm, 3))
            for q in questions:
                q_id = store.insert_question(
                    run_id, item_type=q.item_type, item_key=q.item_key, arm=q.arm,
                    variant_type=q.variant_type, twin_ref=q.twin_ref, question_text=q.question_text,
                    expected_json=json.dumps(q.expected.__dict__), codex_raw_ref=q.codex_raw_ref)
                try:
                    obs = await runner.observe(q.question_text)
                    twin_passed = None
                    if q.arm == "noisy" and q.twin_ref:
                        twin_passed = twin_pass.get((item.item_key, q.twin_ref))
                    outcome = classify(q.expected, obs, q.arm, item.missing_fields, twin_passed,
                                       subject_name=item.display_name)
                    if outcome.graded_soft:
                        v, c = await judge(q.question_text, obs.answer_text, json.dumps(item.ground_truth))
                        outcome.llm_judge_verdict, outcome.llm_judge_confidence = v, c
                    if q.arm == "answer":
                        twin_pass[(item.item_key, q.question_text)] = (outcome.result == "pass")
                    store.insert_result(
                        q_id, answer_text=obs.answer_text,
                        metadata_json=json.dumps({"source_note": obs.source_note, "is_live": obs.is_live}),
                        result=outcome.result, failure_class=outcome.failure_class, data_gap=outcome.data_gap,
                        evidence_json=json.dumps(outcome.evidence), latency_ms=obs.latency_ms,
                        resolved_entity_id=obs.resolved_key, family=obs.family, skill=obs.skill,
                        used_ai=obs.used_ai, graded_soft=outcome.graded_soft,
                        llm_judge_verdict=outcome.llm_judge_verdict, llm_judge_confidence=outcome.llm_judge_confidence)
                except Exception as qe:   # one flaky question (e.g. Ollama timeout) must not kill the run
                    _error_result(store, q_id, qe)
                    print(f"[autoeval] question errored ({item.item_key}): {qe!r}", flush=True)
            store.bump_coverage(item.item_key)
            completed += 1
            write_status(cfg.status_file, "running", run_id=run_id, completed=completed, total=len(items))
    except Exception as e:            # RateLimit exhaustion or any unexpected failure
        errored = e
        print(f"[autoeval] run aborted: {e!r}", flush=True)
    finally:
        await runner.close()
        try:
            gt_conn.close()
        except Exception:
            pass

    rows = store.results_for_run(run_id)
    prev = store.prev_run_at_commit(_kavosh_commit(), run_id)
    prev_rows = store.results_for_run(prev) if prev else None
    report = build_report(rows, prev_rows)
    out_path = Path(cfg.repo_root) / "autoeval" / f"report_run_{run_id}.md"
    out_path.write_text(report, encoding="utf-8")
    final_state = "error" if errored is not None else "done"
    extra = {"reason": repr(errored)} if errored is not None else {}
    write_status(cfg.status_file, final_state, run_id=run_id, completed=completed,
                 total=len(items), report=str(out_path), **extra)
    print(f"run {run_id} {final_state} → {out_path}")
    return run_id

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--items", type=int, default=50)
    args = ap.parse_args()
    cfg = load_config()
    asyncio.run(run_window(cfg, args.items, smoke=args.smoke))

if __name__ == "__main__":
    main()
