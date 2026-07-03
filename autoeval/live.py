# autoeval/live.py
from __future__ import annotations

def format_tail(rows: list[dict]) -> str:
    out = []
    for r in rows:
        verdict = r["result"] if not r.get("failure_class") else f"{r['result']}/{r['failure_class']}"
        out.append(f"[{r['arm']:12}] {verdict:22} Q: {r['question_text'][:60]}\n"
                   f"                              A: {(r.get('answer_text') or '')[:80]}")
    return "\n".join(out) if out else "(no rows yet)"

def format_status(status: dict, running_counts: dict | None = None) -> str:
    state = status.get("state", "unknown")
    line = f"STATE: {state}"
    if state == "paused":
        line += f"   (resume: {status.get('reason', '?')})"
    if status.get("updated_at"):
        line += f"   @ {status['updated_at']}"
    c = running_counts or {}
    counts = (f"\nprogress: {c.get('pass',0)}/{c.get('total',0)} pass   "
              f"fabrication: {c.get('fabrication',0)}") if c else ""
    return line + counts
