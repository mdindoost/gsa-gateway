# eval/processing_debt/run_pilot.py
"""Live pilot driver (R0 per-set + R3 live-fallback-off answer capture). Read-only against the DB;
the only outbound spend is the Brave oracle call (guarded in oracle_brave). Exercised live in Task 14.
"""
from __future__ import annotations
from eval.processing_debt.bootstrap import load_project_env
load_project_env()
import dataclasses
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from eval.processing_debt.dbconn import get_ro_connection
from eval.processing_debt.oracle_brave import ask_oracle
from eval.processing_debt.nuggetize import nuggetize
from eval.processing_debt.xray import xray
from eval.processing_debt.classify import classify_fact

_ANSI = re.compile(r"\x1b\[[0-9;]*m")     # strip terminal color/style escapes
_RULE_CHARS = set("─-=_— \t")             # ─ - = _ — and whitespace

_UNSCOPED_SETS = {"C"}   # Set C is deliberately generic-web; everything else is NJIT-scoped

def njit_scope(question: str) -> str:
    """Give Brave the NJIT context our users have implicitly (their questions target NJIT). Without it the
    oracle answers about the wrong entity (US GSA, other schools' CS chairs, homonymous people)."""
    ql = question.lower()
    if "njit" in ql or "new jersey institute of technology" in ql:
        return question
    return f"{question.rstrip().rstrip('?')} at NJIT (New Jersey Institute of Technology)"


def _extract_answer(raw: str) -> str:
    """Return ONLY the real answer text that ask.sh prints under the 'FINAL LLM ANSWER' header.

    Robust to the real trace_query.py format: ANSI color codes, a parenthetical suffix on the header
    title line, ``─``×72 rule lines, the 2-space answer indent, and the trailing
    ``[source_note=… · used_ai=…]`` diagnostic. No header → "".
    """
    text = _ANSI.sub("", raw)
    if "FINAL LLM ANSWER" not in text:
        return ""
    tail = text.rsplit("FINAL LLM ANSWER", 1)[1]     # last occurrence = the real header
    lines = tail.splitlines()[1:]                    # drop the header-title remainder (its parenthetical)
    while lines and (not lines[0].strip() or set(lines[0]) <= _RULE_CHARS):
        lines.pop(0)                                 # drop leading blank / pure-rule lines
    out = []
    for ln in lines:
        if ln.strip().startswith("[source_note="):
            break                                    # stop at the trailing diagnostic
        out.append(ln[2:] if ln.startswith("  ") else ln)   # de-indent the 2-space answer indent
    return "\n".join(out).strip()


def _default_runner(question: str) -> str:
    """Run the real pipeline with the njit.edu live-fallback OFF (R3) so a fact answered only from the
    live web never counts as IN_ANSWER and no uncounted Brave *Search* credits are spent."""
    env = dict(os.environ)
    env["LIVE_ENABLED"] = "0"
    try:
        return subprocess.run(["bash", "scripts/ask.sh", question, "--answer"],
                              capture_output=True, text=True, timeout=180, env=env).stdout
    except Exception:
        return ""


def _our_answer(question: str, cache: dict, runner) -> str:
    if question in cache:
        return cache[question]
    ans = _extract_answer(runner(question))
    cache[question] = ans
    return ans


def _qkey(question: str, stratum: str) -> str:
    """Stable per-(question, stratum) id for the resume marker."""
    return hashlib.sha1(f"{stratum}\x1f{question}".encode("utf-8")).hexdigest()


def run_pilot(sample_pairs, set_name, *, conn=None, out_dir="eval/processing_debt/out",
              oracle=None, runner=None, resume=True) -> str:
    """Drive one set → facts_{set}.jsonl (one JSON FactRecord per line). CRASH/POWER-SAFE:
    each question's facts are flushed+fsync'd and the question is recorded in done_{set}.txt before moving on,
    so a re-run with resume=True (default) skips finished questions and reuses the on-disk oracle cache.
    resume=False truncates prior output and starts fresh."""
    conn = conn or get_ro_connection()
    oracle = oracle or ask_oracle
    runner = runner or _default_runner
    outdir = Path(out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    facts_path = outdir / f"facts_{set_name}.jsonl"
    done_path = outdir / f"done_{set_name}.txt"
    if not resume:
        facts_path.unlink(missing_ok=True)
        done_path.unlink(missing_ok=True)
    done = set()
    if done_path.exists():
        done = {l.strip() for l in done_path.read_text().splitlines() if l.strip()}
    ans_cache: dict = {}
    with open(facts_path, "a") as ff, open(done_path, "a") as df:
        for question, stratum in sample_pairs:
            qk = _qkey(question, stratum)
            if qk in done:
                continue                                   # already computed on a prior run → skip
            oracle_q = question if set_name in _UNSCOPED_SETS else njit_scope(question)
            oa = oracle(oracle_q)
            if oa.question != question:
                oa = dataclasses.replace(oa, question=question)
            our = _our_answer(question, ans_cache, runner)
            xr = xray(conn, question)
            xr.answer = our
            for nug in nuggetize(oa):
                fr = classify_fact(conn, nug, oa, our, xr, stratum=stratum)
                ff.write(json.dumps(fr.as_dict()) + "\n")
            ff.flush()
            os.fsync(ff.fileno())                          # facts durable against power loss
            df.write(qk + "\n")
            df.flush()
            os.fsync(df.fileno())                          # mark question done AFTER its facts are on disk
            done.add(qk)
    return str(facts_path)
