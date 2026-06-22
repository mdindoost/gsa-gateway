"""Mine distinct past user questions from analytics for router-eval labeling.

The live analytics table is `questions(question_text, timestamp)` (verified 2026-06-21).
CLI emits unlabeled JSONL stubs ({"id","query","family":"?"}) for a human to label.
"""
from __future__ import annotations
import sqlite3, sys, json, re


def harvest(db_path: str, limit: int = 500, since: str | None = None) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sql = "SELECT question_text, timestamp FROM questions"
        params: list = []
        if since:
            sql += " WHERE timestamp >= ?"; params.append(since)
        sql += " ORDER BY timestamp DESC"
        seen, out = set(), []
        for q, _ in conn.execute(sql, params):
            if not q:
                continue
            key = re.sub(r"\s+", " ", q.strip().lower())
            if key in seen:
                continue
            seen.add(key); out.append(q.strip())
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "gsa_gateway.db"
    for i, q in enumerate(harvest(db)):
        print(json.dumps({"id": f"h{i}", "query": q, "family": "?"}))
