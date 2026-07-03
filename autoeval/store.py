from __future__ import annotations
import sqlite3, time
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL, db_snapshot_hash TEXT, config_json TEXT,
  codex_model TEXT, kavosh_commit TEXT, live_enabled INTEGER);
CREATE TABLE IF NOT EXISTS questions (
  q_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, item_type TEXT, item_key TEXT, arm TEXT, variant_type TEXT,
  twin_ref TEXT, question_text TEXT, expected_json TEXT, codex_raw_ref TEXT);
CREATE TABLE IF NOT EXISTS results (
  q_id INTEGER PRIMARY KEY, answer_text TEXT, metadata_json TEXT,
  result TEXT, failure_class TEXT, data_gap INTEGER, evidence_json TEXT,
  latency_ms INTEGER, resolved_entity_id TEXT, family TEXT, skill TEXT,
  used_ai INTEGER, graded_soft INTEGER, llm_judge_verdict TEXT, llm_judge_confidence REAL);
CREATE TABLE IF NOT EXISTS coverage (
  item_key TEXT PRIMARY KEY, times_tested INTEGER DEFAULT 0, last_tested_at REAL);
"""

class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA); self.conn.commit()

    def create_run(self, *, db_snapshot_hash, config_json, codex_model,
                   kavosh_commit, live_enabled) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(started_at,db_snapshot_hash,config_json,codex_model,"
            "kavosh_commit,live_enabled) VALUES(?,?,?,?,?,?)",
            (time.time(), db_snapshot_hash, config_json, codex_model, kavosh_commit,
             int(bool(live_enabled))))
        self.conn.commit(); return cur.lastrowid

    def insert_question(self, run_id, *, item_type, item_key, arm, variant_type,
                        twin_ref, question_text, expected_json, codex_raw_ref) -> int:
        cur = self.conn.execute(
            "INSERT INTO questions(run_id,item_type,item_key,arm,variant_type,twin_ref,"
            "question_text,expected_json,codex_raw_ref) VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, item_type, item_key, arm, variant_type, twin_ref, question_text,
             expected_json, codex_raw_ref))
        self.conn.commit(); return cur.lastrowid

    def insert_result(self, q_id, *, answer_text, metadata_json, result, failure_class,
                      data_gap, evidence_json, latency_ms, resolved_entity_id, family,
                      skill, used_ai, graded_soft, llm_judge_verdict, llm_judge_confidence):
        self.conn.execute(
            "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (q_id, answer_text, metadata_json, result, failure_class, int(bool(data_gap)),
             evidence_json, latency_ms, resolved_entity_id, family, skill, int(bool(used_ai)),
             int(bool(graded_soft)), llm_judge_verdict, llm_judge_confidence))
        self.conn.commit()

    def bump_coverage(self, item_key: str):
        self.conn.execute(
            "INSERT INTO coverage(item_key,times_tested,last_tested_at) VALUES(?,1,?) "
            "ON CONFLICT(item_key) DO UPDATE SET times_tested=times_tested+1,last_tested_at=?",
            (item_key, time.time(), time.time()))
        self.conn.commit()

    def least_tested_keys(self, limit: int, item_type: str | None = None) -> list[str]:
        rows = self.conn.execute(
            "SELECT item_key FROM coverage ORDER BY times_tested ASC, last_tested_at ASC "
            "LIMIT ?", (limit,)).fetchall()
        return [r["item_key"] for r in rows]

    def results_for_run(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT q.*, r.* FROM questions q LEFT JOIN results r ON r.q_id=q.q_id "
            "WHERE q.run_id=?", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def prev_run_at_commit(self, commit: str, before_run_id: int) -> Optional[int]:
        row = self.conn.execute(
            "SELECT run_id FROM runs WHERE kavosh_commit=? AND run_id<? "
            "ORDER BY run_id DESC LIMIT 1", (commit, before_run_id)).fetchone()
        return row["run_id"] if row else None
