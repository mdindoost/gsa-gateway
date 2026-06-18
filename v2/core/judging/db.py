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
                 criteria: list[str] | None = None,
                 top_n: int = 3,
                 score_min: int = 1,
                 score_max: int = 5,
                 min_coverage: int = 3,
                 audience_top_n: int = 1) -> int:
    c = criteria if criteria is not None else DEFAULT_CRITERIA
    cur = conn.execute(
        "INSERT INTO judging_events"
        "(name, criteria, top_n, score_min, score_max, min_coverage, audience_top_n) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, json.dumps(c), top_n, score_min, score_max, min_coverage, audience_top_n),
    )
    return cur.lastrowid


def _row_to_event(r) -> dict:
    return {
        "id": r[0], "name": r[1], "status": r[2],
        "criteria": json.loads(r[3]), "top_n": r[4], "created_at": r[5],
        "score_min": r[6], "score_max": r[7], "min_coverage": r[8],
        "audience_voting": r[9], "audience_top_n": r[10],
    }

_EVENT_COLS = ("id, name, status, criteria, top_n, created_at, "
               "score_min, score_max, min_coverage, audience_voting, audience_top_n")


def get_event(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute(
        f"SELECT {_EVENT_COLS} FROM judging_events WHERE id=?", (event_id,)
    ).fetchone()
    return _row_to_event(row) if row else None


def get_any_event(conn: sqlite3.Connection) -> dict | None:
    """Return the most recently created event regardless of status (for three-state messaging)."""
    row = conn.execute(
        f"SELECT {_EVENT_COLS} FROM judging_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _row_to_event(row) if row else None


def get_open_event(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        f"SELECT {_EVENT_COLS} FROM judging_events WHERE status='open' LIMIT 1"
    ).fetchone()
    return _row_to_event(row) if row else None


def list_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_EVENT_COLS} FROM judging_events ORDER BY id DESC"
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def set_event_status(conn: sqlite3.Connection, event_id: int, status: str) -> None:
    conn.execute("UPDATE judging_events SET status=? WHERE id=?", (status, event_id))


def update_event(conn: sqlite3.Connection, event_id: int,
                 name: str | None = None,
                 criteria: list[str] | None = None,
                 top_n: int | None = None,
                 score_min: int | None = None,
                 score_max: int | None = None,
                 min_coverage: int | None = None,
                 audience_top_n: int | None = None) -> None:
    updates = []
    params = []
    if name is not None:          updates.append("name=?");           params.append(name)
    if criteria is not None:      updates.append("criteria=?");       params.append(json.dumps(criteria))
    if top_n is not None:         updates.append("top_n=?");          params.append(top_n)
    if score_min is not None:     updates.append("score_min=?");      params.append(score_min)
    if score_max is not None:     updates.append("score_max=?");      params.append(score_max)
    if min_coverage is not None:  updates.append("min_coverage=?");   params.append(min_coverage)
    if audience_top_n is not None: updates.append("audience_top_n=?"); params.append(audience_top_n)
    if updates:
        params.append(event_id)
        conn.execute(f"UPDATE judging_events SET {', '.join(updates)} WHERE id=?", params)


def set_audience_voting(conn: sqlite3.Connection, event_id: int, status: str) -> None:
    """Open or close audience voting. status must be 'open' or 'closed'."""
    conn.execute(
        "UPDATE judging_events SET audience_voting=? WHERE id=?", (status, event_id)
    )


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
    tg_hash = _hash(telegram_user_id)
    row = conn.execute(
        "SELECT id, name FROM judging_judges WHERE event_id=? AND telegram_id_hash=?",
        (event_id, tg_hash),
    ).fetchone()
    return {"id": row[0], "name": row[1]} if row else None


def list_judges(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, pin, telegram_id_hash FROM judging_judges "
        "WHERE event_id=? ORDER BY id",
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
        "SELECT number, name, department, is_present, telegram_id_hash "
        "FROM judging_presenters WHERE event_id=? AND number=?",
        (event_id, number),
    ).fetchone()
    if row is None:
        return None
    return {"number": row[0], "name": row[1], "department": row[2],
            "is_present": bool(row[3]), "has_telegram": row[4] is not None}


def list_presenters(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT number, name, department, is_present, telegram_id_hash "
        "FROM judging_presenters WHERE event_id=? ORDER BY number",
        (event_id,),
    ).fetchall()
    return [{"number": r[0], "name": r[1], "department": r[2],
             "is_present": bool(r[3]), "has_telegram": r[4] is not None}
            for r in rows]


def register_presenter(conn: sqlite3.Connection, event_id: int,
                       number: int, telegram_user_id: str) -> bool:
    """Link a presenter's Telegram account to their number. Returns False if already taken by another account."""
    tg_hash = _hash(telegram_user_id)
    row = conn.execute(
        "SELECT telegram_id_hash FROM judging_presenters WHERE event_id=? AND number=?",
        (event_id, number),
    ).fetchone()
    if row is None:
        return False  # presenter number not found
    existing = row[0]
    if existing and existing != tg_hash:
        return False  # number already claimed by a different account
    conn.execute(
        "UPDATE judging_presenters SET telegram_id_hash=?, is_present=1 WHERE event_id=? AND number=?",
        (tg_hash, event_id, number),
    )
    return True


def mark_presenter_present(conn: sqlite3.Connection, event_id: int, number: int) -> None:
    """Admin manually marks a presenter as present (no Telegram link needed)."""
    conn.execute(
        "UPDATE judging_presenters SET is_present=1 WHERE event_id=? AND number=?",
        (event_id, number),
    )


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


def get_score(conn: sqlite3.Connection, event_id: int,
              judge_id: int, presenter_number: int) -> dict | None:
    """Return a judge's submitted scores for a presenter, or None."""
    row = conn.execute(
        "SELECT scores_json, final_score FROM judging_scores "
        "WHERE event_id=? AND judge_id=? AND presenter_number=?",
        (event_id, judge_id, presenter_number),
    ).fetchone()
    if row is None:
        return None
    return {"scores": json.loads(row[0]), "final_score": row[1]}


def get_all_scores_by_judge(conn: sqlite3.Connection,
                             event_id: int, judge_id: int) -> list[dict]:
    """All scores submitted by this judge for this event — for 'my scores' display."""
    rows = conn.execute(
        "SELECT s.presenter_number, p.name, s.scores_json, s.final_score "
        "FROM judging_scores s "
        "JOIN judging_presenters p ON p.event_id=s.event_id AND p.number=s.presenter_number "
        "WHERE s.event_id=? AND s.judge_id=? "
        "ORDER BY s.presenter_number",
        (event_id, judge_id),
    ).fetchall()
    return [{"number": r[0], "name": r[1],
             "scores": json.loads(r[2]), "final_score": r[3]}
            for r in rows]


def get_presenter_scores_detail(conn: sqlite3.Connection,
                                 event_id: int, presenter_number: int) -> list[dict]:
    """Per-judge score breakdown for a presenter — for admin drill-down view."""
    rows = conn.execute(
        "SELECT j.name, s.scores_json, s.final_score, s.submitted_at "
        "FROM judging_scores s "
        "JOIN judging_judges j ON j.id=s.judge_id "
        "WHERE s.event_id=? AND s.presenter_number=? "
        "ORDER BY j.name",
        (event_id, presenter_number),
    ).fetchall()
    return [{"judge_name": r[0], "scores": json.loads(r[1]),
             "final_score": r[2], "submitted_at": r[3]}
            for r in rows]


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
    cur = conn.execute(
        "DELETE FROM judging_scores WHERE event_id=? AND judge_id=? AND presenter_number=?",
        (event_id, judge_id, presenter_number),
    )
    return cur.rowcount > 0


# ── Audience votes ─────────────────────────────────────────────────────────────

def cast_vote(conn: sqlite3.Connection, event_id: int,
              telegram_user_id: str, presenter_number: int) -> None:
    """Cast or replace an audience vote. One vote per person per event."""
    voter_hash = _hash(telegram_user_id)
    conn.execute(
        "INSERT INTO judging_audience_votes(event_id, voter_hash, presenter_number) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(event_id, voter_hash) DO UPDATE SET "
        "presenter_number=excluded.presenter_number, voted_at=datetime('now')",
        (event_id, voter_hash, presenter_number),
    )


def get_vote(conn: sqlite3.Connection,
             event_id: int, telegram_user_id: str) -> dict | None:
    """Return the current vote for this user, or None."""
    voter_hash = _hash(telegram_user_id)
    row = conn.execute(
        "SELECT v.presenter_number, p.name, p.department "
        "FROM judging_audience_votes v "
        "JOIN judging_presenters p ON p.event_id=v.event_id AND p.number=v.presenter_number "
        "WHERE v.event_id=? AND v.voter_hash=?",
        (event_id, voter_hash),
    ).fetchone()
    if row is None:
        return None
    return {"presenter_number": row[0], "name": row[1], "department": row[2]}


def get_audience_results(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Presenters sorted by vote count descending. Ties share rank."""
    rows = conn.execute(
        """
        SELECT p.number, p.name, p.department, COUNT(v.voter_hash) AS vote_count
        FROM judging_presenters p
        LEFT JOIN judging_audience_votes v
               ON v.event_id = p.event_id AND v.presenter_number = p.number
        WHERE p.event_id = ?
        GROUP BY p.number, p.name, p.department
        ORDER BY vote_count DESC, p.number
        """,
        (event_id,),
    ).fetchall()

    results = []
    rank = 1
    for i, r in enumerate(rows):
        count = r[3]
        if count == 0:
            current_rank = None
        elif i > 0 and count == rows[i - 1][3] and rows[i - 1][3] > 0:
            current_rank = results[-1]["rank"]
        else:
            current_rank = rank
        results.append({
            "rank": current_rank,
            "number": r[0],
            "name": r[1],
            "department": r[2],
            "vote_count": count,
        })
        if count > 0:
            rank += 1
    return results
