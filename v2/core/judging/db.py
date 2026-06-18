"""Judging system DB layer — CRUD for events, judges, presenters, scores."""
from __future__ import annotations

import hashlib
import json
import sqlite3

DEFAULT_CRITERIA = [
    "Communication & Clarity",
    "Research Content",
    "Delivery & Engagement",
    "Organization & Timing",
    "Visual Slide Effectiveness",
    "Overall Impression",
]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── Events ────────────────────────────────────────────────────────────────────

def create_event(conn: sqlite3.Connection, name: str,
                 criteria: list[str] | None = None, top_n: int = 3) -> int:
    c = criteria if criteria is not None else DEFAULT_CRITERIA
    cur = conn.execute(
        "INSERT INTO judging_events(name, criteria, top_n) VALUES (?, ?, ?)",
        (name, json.dumps(c), top_n),
    )
    return cur.lastrowid


def get_event(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, name, status, criteria, top_n, created_at FROM judging_events WHERE id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "status": row[2],
            "criteria": json.loads(row[3]), "top_n": row[4], "created_at": row[5]}


def get_open_event(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, name, status, criteria, top_n FROM judging_events WHERE status='open' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "status": row[2],
            "criteria": json.loads(row[3]), "top_n": row[4]}


def list_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, status, criteria, top_n, created_at FROM judging_events ORDER BY id DESC"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "status": r[2],
             "criteria": json.loads(r[3]), "top_n": r[4], "created_at": r[5]}
            for r in rows]


def set_event_status(conn: sqlite3.Connection, event_id: int, status: str) -> None:
    conn.execute("UPDATE judging_events SET status=? WHERE id=?", (status, event_id))


def update_event(conn: sqlite3.Connection, event_id: int,
                 name: str | None = None,
                 criteria: list[str] | None = None,
                 top_n: int | None = None) -> None:
    if name is not None:
        conn.execute("UPDATE judging_events SET name=? WHERE id=?", (name, event_id))
    if criteria is not None:
        conn.execute("UPDATE judging_events SET criteria=? WHERE id=?",
                     (json.dumps(criteria), event_id))
    if top_n is not None:
        conn.execute("UPDATE judging_events SET top_n=? WHERE id=?", (top_n, event_id))


# ── Judges ────────────────────────────────────────────────────────────────────

def add_judge(conn: sqlite3.Connection, event_id: int, name: str, pin: str) -> int:
    cur = conn.execute(
        "INSERT INTO judging_judges(event_id, name, pin) VALUES (?, ?, ?)",
        (event_id, name, pin),
    )
    return cur.lastrowid


def authenticate_judge(conn: sqlite3.Connection, event_id: int,
                       pin: str, telegram_user_id: str) -> dict | None:
    """Verify PIN. Records telegram_id_hash on first use. Returns judge dict or None."""
    row = conn.execute(
        "SELECT id, name, telegram_id_hash FROM judging_judges WHERE event_id=? AND pin=?",
        (event_id, pin),
    ).fetchone()
    if row is None:
        return None
    judge_id, name, existing_hash = row[0], row[1], row[2]
    tg_hash = _hash(telegram_user_id)
    if existing_hash and existing_hash != tg_hash:
        return None  # PIN already claimed by a different Telegram account
    if not existing_hash:
        conn.execute(
            "UPDATE judging_judges SET telegram_id_hash=? WHERE id=?",
            (tg_hash, judge_id),
        )
    return {"id": judge_id, "name": name}


def get_judge_by_telegram_hash(conn: sqlite3.Connection,
                                event_id: int, telegram_user_id: str) -> dict | None:
    """Return judge dict if this Telegram user is already authenticated for the event."""
    tg_hash = _hash(telegram_user_id)
    row = conn.execute(
        "SELECT id, name FROM judging_judges WHERE event_id=? AND telegram_id_hash=?",
        (event_id, tg_hash),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1]}


def list_judges(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, pin, telegram_id_hash FROM judging_judges WHERE event_id=? ORDER BY id",
        (event_id,),
    ).fetchall()
    return [{"id": r[0], "name": r[1], "pin": r[2], "authenticated": r[3] is not None}
            for r in rows]


def delete_judge(conn: sqlite3.Connection, judge_id: int) -> None:
    conn.execute("DELETE FROM judging_judges WHERE id=?", (judge_id,))


# ── Presenters ────────────────────────────────────────────────────────────────

def load_presenters_csv(conn: sqlite3.Connection, event_id: int, csv_text: str) -> int:
    """Parse CSV (number,name,department — header optional). Returns count inserted."""
    lines = [ln.strip() for ln in csv_text.strip().splitlines() if ln.strip()]
    if not lines:
        return 0
    start = 1 if lines[0].lower().startswith("number") else 0
    count = 0
    for line in lines[start:]:
        parts = line.split(",", 2)
        if len(parts) < 2:
            continue
        try:
            number = int(parts[0].strip())
        except ValueError:
            continue
        name = parts[1].strip()
        dept = parts[2].strip() if len(parts) > 2 else ""
        conn.execute(
            "INSERT OR REPLACE INTO judging_presenters(event_id, number, name, department) "
            "VALUES (?,?,?,?)",
            (event_id, number, name, dept),
        )
        count += 1
    return count


def get_presenter(conn: sqlite3.Connection, event_id: int, number: int) -> dict | None:
    row = conn.execute(
        "SELECT number, name, department FROM judging_presenters WHERE event_id=? AND number=?",
        (event_id, number),
    ).fetchone()
    if row is None:
        return None
    return {"number": row[0], "name": row[1], "department": row[2]}


def list_presenters(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT number, name, department FROM judging_presenters WHERE event_id=? ORDER BY number",
        (event_id,),
    ).fetchall()
    return [{"number": r[0], "name": r[1], "department": r[2]} for r in rows]


def delete_presenter(conn: sqlite3.Connection, event_id: int, number: int) -> None:
    conn.execute(
        "DELETE FROM judging_presenters WHERE event_id=? AND number=?",
        (event_id, number),
    )


# ── Scores ────────────────────────────────────────────────────────────────────

def has_scored(conn: sqlite3.Connection, event_id: int,
               judge_id: int, presenter_number: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM judging_scores WHERE event_id=? AND judge_id=? AND presenter_number=?",
        (event_id, judge_id, presenter_number),
    ).fetchone()
    return row is not None


def submit_score(conn: sqlite3.Connection, event_id: int, judge_id: int,
                 presenter_number: int, criteria: list[str], scores: list[int]) -> None:
    scores_json = json.dumps(dict(zip(criteria, scores)))
    final = sum(scores) / len(scores)
    conn.execute(
        "INSERT INTO judging_scores"
        "(event_id, judge_id, presenter_number, scores_json, final_score) "
        "VALUES (?,?,?,?,?)",
        (event_id, judge_id, presenter_number, scores_json, final),
    )


def delete_score(conn: sqlite3.Connection, event_id: int,
                 judge_id: int, presenter_number: int) -> bool:
    """Delete a score so the judge can re-score. Returns True if a row was deleted."""
    cur = conn.execute(
        "DELETE FROM judging_scores WHERE event_id=? AND judge_id=? AND presenter_number=?",
        (event_id, judge_id, presenter_number),
    )
    return cur.rowcount > 0
