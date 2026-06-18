"""Score aggregation and export for the judging system."""
from __future__ import annotations

import json
import sqlite3


def get_leaderboard(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Presenters sorted by mean final_score descending. Unscored appear at bottom with rank=None."""
    rows = conn.execute(
        """
        SELECT p.number, p.name, p.department,
               COUNT(s.id)        AS judge_count,
               AVG(s.final_score) AS avg_score
        FROM judging_presenters p
        LEFT JOIN judging_scores s
               ON s.event_id = p.event_id AND s.presenter_number = p.number
        WHERE p.event_id = ?
        GROUP BY p.number, p.name, p.department
        ORDER BY avg_score DESC, p.number
        """,
        (event_id,),
    ).fetchall()

    results = []
    rank = 1
    for i, r in enumerate(rows):
        avg = r[4]
        if avg is None:
            current_rank = None
        elif i > 0 and avg == rows[i - 1][4] and rows[i - 1][4] is not None:
            current_rank = results[-1]["rank"]
        else:
            current_rank = rank
        results.append({
            "rank": current_rank,
            "number": r[0],
            "name": r[1],
            "department": r[2],
            "judge_count": r[3],
            "avg_score": round(avg, 3) if avg is not None else None,
        })
        if avg is not None:
            rank += 1
    return results


def get_event_progress(conn: sqlite3.Connection, event_id: int) -> dict:
    total_judges = conn.execute(
        "SELECT COUNT(*) FROM judging_judges WHERE event_id=?", (event_id,)
    ).fetchone()[0]
    auth_judges = conn.execute(
        "SELECT COUNT(*) FROM judging_judges "
        "WHERE event_id=? AND telegram_id_hash IS NOT NULL",
        (event_id,),
    ).fetchone()[0]
    total_presenters = conn.execute(
        "SELECT COUNT(*) FROM judging_presenters WHERE event_id=?", (event_id,)
    ).fetchone()[0]
    scores_submitted = conn.execute(
        "SELECT COUNT(*) FROM judging_scores WHERE event_id=?", (event_id,)
    ).fetchone()[0]
    max_possible = total_judges * total_presenters
    coverage_pct = (
        round(scores_submitted / max_possible * 100, 1) if max_possible > 0 else 0.0
    )
    return {
        "total_judges": total_judges,
        "authenticated_judges": auth_judges,
        "total_presenters": total_presenters,
        "scores_submitted": scores_submitted,
        "max_possible": max_possible,
        "coverage_pct": coverage_pct,
    }


def export_csv(conn: sqlite3.Connection, event_id: int) -> str:
    """Return a CSV string of the full leaderboard with per-criterion averages."""
    event_row = conn.execute(
        "SELECT criteria FROM judging_events WHERE id=?", (event_id,)
    ).fetchone()
    criteria: list[str] = json.loads(event_row[0]) if event_row else []

    def _col(c: str) -> str:
        return "avg_" + c.lower().replace(" & ", "_and_").replace(" ", "_")

    header = (
        ["rank", "number", "name", "department"]
        + [_col(c) for c in criteria]
        + ["final_score", "judge_count"]
    )
    lines = [",".join(header)]

    for row in get_leaderboard(conn, event_id):
        score_rows = conn.execute(
            "SELECT scores_json FROM judging_scores "
            "WHERE event_id=? AND presenter_number=?",
            (event_id, row["number"]),
        ).fetchall()
        per_crit: dict[str, list[float]] = {c: [] for c in criteria}
        for sr in score_rows:
            d = json.loads(sr[0])
            for c in criteria:
                if c in d:
                    per_crit[c].append(float(d[c]))

        line = [
            str(row["rank"] if row["rank"] is not None else ""),
            str(row["number"]),
            f'"{row["name"]}"',
            f'"{row["department"]}"',
        ]
        for c in criteria:
            vals = per_crit[c]
            line.append(f"{sum(vals)/len(vals):.2f}" if vals else "")
        line += [
            f"{row['avg_score']:.3f}" if row["avg_score"] is not None else "",
            str(row["judge_count"]),
        ]
        lines.append(",".join(line))

    return "\n".join(lines)
