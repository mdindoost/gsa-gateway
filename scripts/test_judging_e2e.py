#!/usr/bin/env python3
"""End-to-end simulation of a full 3MRP judging event.

6 simulated personas (no live Telegram — sessions go directly through the
same JudgingSessionManager the bot uses):

  ADMIN   — direct jdb calls (mirrors what local_server.py does)
  AMIRA   — judge 1: full flow, logout-during-scoring (H1), submit-after-close (H-new-2)
  BOB     — judge 2: double-submit race condition (C2)
  CARLOS  — judge 3: PIN brute-force lockout (H3)
  ALICE   — audience voter: happy path + vote change
  DAVE    — audience voter: H-new-1 (yes after audience voting closed)

Also exercises:
  Presenter registration (duplicate number rejection)
  DB-layer: score range gate (M-new-5), presenter FK (M-new-2), CSV row cap (M-new-6)
  Leaderboard and audience ranks (H5 standard competition rank)

Run: python scripts/test_judging_e2e.py
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile

# Fast scrypt — must be set before importing jdb (it reads at module load time)
os.environ["GSA_JUDGING_SCRYPT_N"] = "64"

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.core.database.schema import create_all
from v2.core.judging import db as jdb
from v2.core.judging.session import JudgingSessionManager
from v2.core.judging.calculator import get_leaderboard, export_csv
from v2.core.judging.db import get_audience_results

# ── result tracking ────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0


def ok(label: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  ✓  {label}")


def fail(label: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    print(f"  ✗  {label}")
    if detail:
        print(f"        got: {detail}")


def check(label: str, condition: bool, got=None) -> None:
    if condition:
        ok(label)
    else:
        fail(label, repr(got) if got is not None else "")


def section(title: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {title}")
    print("─" * 64)


# ── helpers ────────────────────────────────────────────────────────────────────

def db_conn(db_path: str) -> sqlite3.Connection:
    return create_all(db_path)


def admin_open_audience(db_path: str, eid: int) -> None:
    conn = db_conn(db_path)
    jdb.set_audience_voting(conn, eid, "open")
    conn.commit()
    conn.close()


def admin_close_audience(db_path: str, eid: int) -> None:
    conn = db_conn(db_path)
    jdb.set_audience_voting(conn, eid, "closed")
    conn.commit()
    conn.close()


def admin_close_event(db_path: str, eid: int) -> None:
    conn = db_conn(db_path)
    jdb.set_event_status(conn, eid, "closed")
    conn.commit()
    conn.close()


def admin_reopen_event(db_path: str, eid: int) -> None:
    conn = db_conn(db_path)
    jdb.set_event_status(conn, eid, "open")
    conn.commit()
    conn.close()


# ── main simulation ─────────────────────────────────────────────────────────────

def run_simulation() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _simulate(db_path)
    finally:
        os.unlink(db_path)
        print(f"\n{'=' * 64}")
        total = _PASS + _FAIL
        print(f"  {_PASS}/{total} checks passed"
              + (f" — {_FAIL} FAILED" if _FAIL else " — all good"))
        print("=" * 64)
        if _FAIL:
            sys.exit(1)


def _simulate(db_path: str) -> None:

    # ── ADMIN: create event + load presenters + add judges + open ──────────────
    section("ADMIN — Event Setup")

    conn = db_conn(db_path)
    eid = jdb.create_event(
        conn, "3MRP E2E Simulation",
        criteria=["Clarity", "Content"],
        top_n=2,
        score_min=1, score_max=5,
        min_coverage=2,
        audience_top_n=1,
    )

    loaded = jdb.load_presenters_csv(
        conn, eid,
        "100,Alice Chen,Computer Science\n"
        "101,Bob Kumar,Electrical Engineering\n"
        "102,Carol Diaz,Biology\n"
        "103,Dave Park,Physics\n",
    )
    check("4 presenters loaded", loaded == 4, loaded)

    jdb.add_judge(conn, eid, "Amira Khalil", "AM-001")
    jdb.add_judge(conn, eid, "Bob Chen",     "BO-002")
    jdb.add_judge(conn, eid, "Carlos Vera",  "CV-003")
    jdb.set_event_status(conn, eid, "open")
    conn.commit()
    conn.close()
    ok("Event opened with 4 presenters and 3 judges")

    judges = db_conn(db_path).execute(
        "SELECT name, has_pin FROM (SELECT name, 1 AS has_pin FROM judging_judges WHERE event_id=?)",
        (eid,),
    ).fetchall()

    # Verify PINs are never stored in plaintext
    raw_pins_in_db = db_conn(db_path).execute(
        "SELECT COUNT(*) FROM judging_judges WHERE pin IN ('AM-001','BO-002','CV-003')",
        (),
    ).fetchone()[0]
    check("C1: PINs are hashed (not stored plaintext)", raw_pins_in_db == 0, raw_pins_in_db)

    mgr = JudgingSessionManager(db_path)

    # ── AMIRA — Full happy path ────────────────────────────────────────────────
    section("AMIRA (Judge 1) — Auth + Score 100 + Score 101")

    r, c = mgr.handle("tg_amira", "judge mode")
    check("judge mode prompts for PIN", c and "PIN" in r, r)

    r, c = mgr.handle("tg_amira", "AM-001")
    check("correct PIN authenticates", c and "Amira Khalil" in r, r)
    check("score range shown on auth", "1" in r and "5" in r, r)

    # Score presenter 100
    r, _ = mgr.handle("tg_amira", "100")
    check("confirm prompt for #100 (Alice Chen)", "Alice Chen" in r and "correct" in r.lower(), r)
    r, _ = mgr.handle("tg_amira", "yes")
    check("yes starts scoring #100", "Q1" in r, r)
    mgr.handle("tg_amira", "4")
    r, _ = mgr.handle("tg_amira", "5")
    check("confirmation shows Total: 9/10", "9/10" in r, r)
    r, _ = mgr.handle("tg_amira", "yes")
    check("score submitted for #100", "submitted" in r.lower(), r)

    # Score presenter 101
    r, _ = mgr.handle("tg_amira", "101")
    check("confirm prompt for #101 (Bob Kumar)", "Bob Kumar" in r, r)
    mgr.handle("tg_amira", "yes")
    mgr.handle("tg_amira", "3")
    mgr.handle("tg_amira", "4")
    r, _ = mgr.handle("tg_amira", "yes")
    check("score submitted for #101", "submitted" in r.lower(), r)

    # Re-score check (already scored)
    r, _ = mgr.handle("tg_amira", "100")
    check("already-scored shows previous values (4 and 5)", "4" in r and "5" in r, r)
    check("already-scored shows Total:", "Total:" in r, r)
    check("already-scored says 'already'", "already" in r.lower(), r)

    # my scores
    r, _ = mgr.handle("tg_amira", "my scores")
    check("my scores shows 2 presenters", "2 presenter" in r, r)
    check("my scores lists #100", "100" in r, r)
    check("my scores lists #101", "101" in r, r)

    # ── BOB — Double-submit C2 ────────────────────────────────────────────────
    section("BOB (Judge 2) — Auth + Score + Duplicate-submit (C2)")

    mgr.handle("tg_bob_j", "judge mode")
    r, _ = mgr.handle("tg_bob_j", "BO-002")
    check("Bob authenticated", "Bob Chen" in r, r)

    mgr.handle("tg_bob_j", "100")
    mgr.handle("tg_bob_j", "yes")        # confirm presenter
    mgr.handle("tg_bob_j", "5")
    mgr.handle("tg_bob_j", "5")
    r, _ = mgr.handle("tg_bob_j", "yes")
    check("Bob: score #100 submitted", "submitted" in r.lower(), r)

    # Simulate Telegram double-tap: put session back in confirming with same presenter
    sess = mgr._sessions.get("tg_bob_j")
    if sess:
        sess.state = "confirming"
        sess.presenter_number = 100
        sess.presenter_name = "Alice Chen"
        sess.presenter_dept = "Computer Science"
        sess.collected_scores = [5, 5]
    r, _ = mgr.handle("tg_bob_j", "yes")
    check("C2: duplicate submit returns clear message", "already" in r.lower(), r)
    after_sess = mgr._sessions.get("tg_bob_j")
    check("C2: session recovers to ready state",
          after_sess is not None and after_sess.state == "ready",
          after_sess.state if after_sess else "session gone")

    # Bob can continue scoring after C2 recovery
    r, _ = mgr.handle("tg_bob_j", "101")
    check("Bob continues scoring after C2 recovery (#101 shown)", "Bob Kumar" in r, r)
    mgr.handle("tg_bob_j", "yes")        # confirm presenter
    mgr.handle("tg_bob_j", "4")
    mgr.handle("tg_bob_j", "4")
    r, _ = mgr.handle("tg_bob_j", "yes")
    check("Bob: score #101 submitted", "submitted" in r.lower(), r)

    # ── CARLOS — PIN lockout H3 ────────────────────────────────────────────────
    section("CARLOS (Judge 3) — PIN Lockout (H3) + Auth + Score #102")

    mgr.handle("tg_carlos", "judge mode")

    for attempt in range(1, 5):
        r, _ = mgr.handle("tg_carlos", "WRONGPIN")
        check(f"wrong PIN attempt {attempt}: invalid (not yet locked)", "Invalid" in r, r)

    # 5th wrong attempt triggers lockout
    r, _ = mgr.handle("tg_carlos", "WRONGPIN")
    check("H3: 5th wrong PIN triggers lockout message", "Too many" in r, r)

    # Correct PIN is still rejected while locked out
    r, _ = mgr.handle("tg_carlos", "CV-003")
    check("H3: correct PIN refused while locked", "Too many" in r or "minute" in r.lower(), r)

    # Force-expire lockout (simulates waiting 10 minutes)
    sess = mgr._sessions.get("tg_carlos")
    if sess:
        sess.pin_locked_until = 0.0
        sess.pin_attempts = 0

    r, _ = mgr.handle("tg_carlos", "CV-003")
    check("H3: correct PIN accepted after lockout expires", "Carlos Vera" in r, r)

    r, _ = mgr.handle("tg_carlos", "102")
    check("Carlos confirm prompt #102 (Carol Diaz)", "Carol Diaz" in r, r)
    mgr.handle("tg_carlos", "yes")       # confirm presenter
    mgr.handle("tg_carlos", "3")
    mgr.handle("tg_carlos", "4")
    r, _ = mgr.handle("tg_carlos", "yes")
    check("Carlos: score #102 submitted", "submitted" in r.lower(), r)

    # ── AMIRA — Logout during scoring H1 ──────────────────────────────────────
    section("AMIRA — Logout During Scoring (H1)")

    # Amira is still in ready state (never exited) — typing a number takes her straight to scoring
    # Confirm she's in judge context by checking the manager state directly
    amira_in_ready = mgr._sessions.get("tg_amira") and mgr._sessions["tg_amira"].state == "ready"
    check("Amira still in ready state (no re-auth needed)", amira_in_ready, mgr._sessions.get("tg_amira"))

    r, _ = mgr.handle("tg_amira", "102")
    check("Amira confirm prompt #102 (Carol Diaz)", "Carol Diaz" in r, r)
    mgr.handle("tg_amira", "yes")        # confirm presenter → scoring
    r, _ = mgr.handle("tg_amira", "4")
    check("Q1 accepted, Q2 prompt shown", "Q2" in r or "Content" in r, r)

    # Exit mid-scoring — should warn about lost in-progress scores
    r, _ = mgr.handle("tg_amira", "exit judge mode")
    check("H1: exit mid-scoring warns scores NOT saved",
          "NOT saved" in r or "not saved" in r.lower(), r)
    check("H1: warning names the presenter (102 or Carol)", "102" in r or "Carol" in r, r)

    # Session is now idle
    r, c = mgr.handle("tg_amira", "Who is the GSA president?")
    check("H1: session returns to idle after mid-scoring exit", not c, c)

    # ── PRESENTER — Registration flow ─────────────────────────────────────────
    section("PRESENTER — Registration + Duplicate Rejection")

    r, _ = mgr.handle("tg_pres_100", "presenter mode")
    check("presenter mode prompts for number", "number" in r.lower(), r)

    r, _ = mgr.handle("tg_pres_100", "100")
    check("presenter #100 registered (Alice Chen)", "Alice Chen" in r and "100" in r, r)

    # Second user tries to claim same number
    mgr.handle("tg_pres_imp", "presenter mode")
    r, _ = mgr.handle("tg_pres_imp", "100")
    check("duplicate #100 rejected for different account",
          "already registered" in r.lower() or "different account" in r.lower(), r)

    # Back to idle after rejection (session stays in presenter_awaiting_number — can retry)
    # Verify tg_pres_100 is now idle
    r, c = mgr.handle("tg_pres_100", "Who runs the GSA?")
    check("presenter session idle after successful registration", not c, c)

    # ── AUDIENCE — Alice votes, Dave H-new-1 ──────────────────────────────────
    section("ALICE — Audience Voting (Happy Path + Vote Change)")

    # Audience voting not yet open
    r, _ = mgr.handle("tg_alice_a", "audience mode")
    check("audience mode refused before voting opens", "not active" in r.lower(), r)

    admin_open_audience(db_path, eid)
    ok("Admin opens audience voting")

    r, _ = mgr.handle("tg_alice_a", "audience mode")
    check("Alice enters audience mode", "Audience Mode" in r, r)

    r, _ = mgr.handle("tg_alice_a", "100")
    check("Alice selects #100 for confirmation", "Alice Chen" in r, r)

    r, _ = mgr.handle("tg_alice_a", "yes")
    check("Alice's vote for #100 cast", "Vote cast" in r or "cast" in r.lower(), r)

    # Alice changes her vote: re-enter audience mode, shows previous vote
    r, _ = mgr.handle("tg_alice_a", "audience mode")
    check("re-enter shows previous vote", "previously" in r.lower() or "100" in r, r)

    r, _ = mgr.handle("tg_alice_a", "101")
    r, _ = mgr.handle("tg_alice_a", "yes")
    check("Alice's vote updated to #101", "cast" in r.lower() or "Bob Kumar" in r, r)

    conn = db_conn(db_path)
    alice_vote = jdb.get_vote(conn, eid, "tg_alice_a")
    conn.close()
    check("DB: Alice's final vote is #101", alice_vote is not None and
          alice_vote["presenter_number"] == 101, alice_vote)

    # ── DAVE — H-new-1: vote after audience voting closed ─────────────────────
    section("DAVE — H-new-1: Vote Blocked After Audience Voting Closed")

    mgr.handle("tg_dave_a", "audience mode")
    r, _ = mgr.handle("tg_dave_a", "103")
    check("Dave selects #103 (in audience_confirming state)", "Dave Park" in r, r)

    # Admin closes audience voting while Dave is mid-confirmation
    admin_close_audience(db_path, eid)
    ok("Admin closes audience voting (Dave is still in confirming state)")

    r, _ = mgr.handle("tg_dave_a", "yes")
    check("H-new-1: yes blocked after audience voting closed",
          "closed" in r.lower() and ("NOT recorded" in r or "not" in r.lower()), r)

    conn = db_conn(db_path)
    dave_vote = jdb.get_vote(conn, eid, "tg_dave_a")
    conn.close()
    check("H-new-1: Dave's vote NOT in DB", dave_vote is None, dave_vote)

    # ── AMIRA — H-new-2: submit score after event closed ──────────────────────
    section("AMIRA — H-new-2: Score Submit Blocked After Event Closed")

    # Re-enter judge mode (Amira is still linked in DB)
    r, _ = mgr.handle("tg_amira", "judge mode")
    check("Amira re-enters judge mode (welcome back)", "Welcome back" in r, r)

    r, _ = mgr.handle("tg_amira", "103")
    check("Amira confirm prompt #103 (Dave Park)", "Dave Park" in r, r)
    mgr.handle("tg_amira", "yes")        # confirm presenter → scoring
    mgr.handle("tg_amira", "4")
    r, _ = mgr.handle("tg_amira", "5")
    check("all criteria answered — confirmation shown", "Total:" in r and "yes" in r.lower(), r)

    # Admin closes event while Amira is in confirming state
    admin_close_event(db_path, eid)
    ok("Admin closes event (Amira is still in confirming state)")

    r, _ = mgr.handle("tg_amira", "yes")
    check("H-new-2: submit blocked after event closed",
          "closed" in r.lower() and "NOT saved" in r, r)

    # Verify #103 score was NOT written to DB
    conn = db_conn(db_path)
    amira_judge = jdb.get_judge_by_telegram_hash(conn, eid, "tg_amira")
    score_103 = jdb.has_scored(conn, eid, amira_judge["id"], 103) if amira_judge else None
    conn.close()
    check("H-new-2: #103 score absent from DB", score_103 is False, score_103)

    # Amira's session should be back in ready state (not stuck in confirming)
    amira_sess = mgr._sessions.get("tg_amira")
    check("H-new-2: session recovers to ready after close",
          amira_sess is not None and amira_sess.state == "ready",
          amira_sess.state if amira_sess else "session gone")

    # ── FINAL RESULTS ─────────────────────────────────────────────────────────
    section("LEADERBOARD — Rank Verification (H5 Standard Competition Rank)")

    conn = db_conn(db_path)
    board = get_leaderboard(conn, eid, min_coverage=2)
    conn.close()

    by_num = {r["number"]: r for r in board}

    # 100: Amira(4+5=4.5 avg) + Bob(5+5=5.0 avg) → overall avg = 4.75 → rank 1
    # 101: Amira(3+4=3.5 avg) + Bob(4+4=4.0 avg) → overall avg = 3.75 → rank 2
    # 102: Carlos(3+4=3.5 avg) only → avg = 3.5, low_coverage (1 judge < 2) → rank 3
    # 103: no scores → rank None
    check("#100 is rank 1 (avg 4.75)", by_num[100]["rank"] == 1, by_num[100])
    check("#101 is rank 2 (avg 3.75)", by_num[101]["rank"] == 2, by_num[101])
    check("#102 is rank 3 (low coverage)", by_num[102]["rank"] == 3, by_num[102])
    check("#102 flagged low_coverage", by_num[102]["low_coverage"] is True, by_num[102])
    check("#103 has rank None (unscored)", by_num[103]["rank"] is None, by_num[103])

    section("AUDIENCE RESULTS — Vote Counts + Rank")

    conn = db_conn(db_path)
    aud = get_audience_results(conn, eid)
    conn.close()

    aud_map = {r["number"]: r for r in aud}

    # Alice changed vote from 100 → 101; Dave's vote was blocked → 0 votes
    check("#101 has 1 audience vote (Alice's final vote)",
          aud_map[101]["vote_count"] == 1, aud_map[101])
    check("#100 has 0 audience votes (Alice changed away)",
          aud_map[100]["vote_count"] == 0, aud_map[100])
    check("#103 has 0 audience votes (Dave's vote blocked)",
          aud_map[103]["vote_count"] == 0, aud_map[103])
    check("#101 is audience rank 1", aud_map[101]["rank"] == 1, aud_map[101])
    check("0-vote presenters have rank None",
          all(aud_map[n]["rank"] is None for n in (100, 102, 103)),
          {n: aud_map[n]["rank"] for n in (100, 102, 103)})

    # ── DB LAYER GUARDS ────────────────────────────────────────────────────────
    section("DB LAYER — Score Range Gate (M-new-5)")

    conn = db_conn(db_path)
    # Re-open temporarily just to add a test presenter
    jdb.set_event_status(conn, eid, "open")
    jdb.load_presenters_csv(conn, eid, "200,Test Person,Test")
    conn.commit()
    amira_judge2 = jdb.get_judge_by_telegram_hash(conn, eid, "tg_amira")
    try:
        jdb.submit_score(conn, eid, amira_judge2["id"], 200, ["Clarity", "Content"], [10, 10])
        fail("M-new-5: out-of-range score should raise ValueError")
    except ValueError as e:
        check("M-new-5: score=10 on [1-5] event raises ValueError",
              "range" in str(e).lower() or "10" in str(e), str(e))
    finally:
        conn.close()

    section("DB LAYER — Presenter FK Constraint (M-new-2)")

    conn = db_conn(db_path)
    amira_judge3 = jdb.get_judge_by_telegram_hash(conn, eid, "tg_amira")
    try:
        jdb.submit_score(conn, eid, amira_judge3["id"], 999, ["Clarity", "Content"], [3, 4])
        fail("M-new-2: score for non-existent presenter should raise")
    except (sqlite3.IntegrityError, ValueError):
        check("M-new-2: score for presenter #999 (absent) raises FK error", True)
    finally:
        conn.close()

    section("DB LAYER — CSV Row Cap (M-new-6)")

    conn = db_conn(db_path)
    big_csv = "\n".join(f"{i},Person {i},Dept" for i in range(1000, 1502))
    try:
        jdb.load_presenters_csv(conn, eid, big_csv)
        fail("M-new-6: 502-row CSV should raise ValueError")
    except ValueError as e:
        check("M-new-6: 502-row CSV raises ValueError (max 500)",
              "500" in str(e) or "large" in str(e).lower(), str(e))
    finally:
        conn.close()

    section("DB LAYER — No-open-event 'judge mode' message")

    # Close the event and verify a fresh user gets a proper message
    admin_close_event(db_path, eid)
    mgr2 = JudgingSessionManager(db_path)
    r, c = mgr2.handle("newuser_fresh", "judge mode")
    check("Closed event gives 'closed' message to fresh user",
          c and "closed" in r.lower(), r)

    section("EXPORT — CSV generation smoke-check")

    conn = db_conn(db_path)
    csv_out = export_csv(conn, eid)
    conn.close()
    check("export_csv returns non-empty string", bool(csv_out))
    check("export_csv header has 'rank'", "rank" in csv_out.split("\n")[0])
    check("export_csv has data rows (>1 lines)", len(csv_out.strip().splitlines()) > 1)
    check("export_csv includes presenter 100",
          any("100" in line for line in csv_out.splitlines()))

    # ── AUDIT TRAIL (Task #4) — judge submits were logged via the session layer ──
    section("AUDIT TRAIL — judge submits recorded")

    conn = db_conn(db_path)
    trail = jdb.get_score_audit(conn, eid)
    # Admin enter + edit on top of the judge submits, then verify it all logs.
    amira = jdb.get_judge_by_telegram_hash(conn, eid, "tg_amira")
    existed, _, final = jdb.upsert_score(conn, eid, amira["id"], 103, ["Clarity", "Content"], [5, 5])
    jdb.log_score_audit(conn, eid, amira["id"], 103,
                        action="admin_edit" if existed else "admin_enter",
                        actor="admin", actor_label="admin", scores_json='{"Clarity": 5, "Content": 5}',
                        final_score=final)
    conn.commit()
    trail2 = jdb.get_score_audit(conn, eid)
    conn.close()

    submit_rows = [a for a in trail if a["action"] == "submit"]
    check("judge submits were audited (>=4 submit rows)", len(submit_rows) >= 4,
          f"{len(submit_rows)} submit rows")
    check("audit actor is 'judge' for submits", all(a["actor"] == "judge" for a in submit_rows),
          submit_rows[:1])
    check("admin proxy entry for #103 logged as admin_enter",
          any(a["action"] == "admin_enter" and a["presenter_number"] == 103 for a in trail2),
          [a["action"] for a in trail2[:3]])
    check("admin entry actor is 'admin'",
          any(a["actor"] == "admin" for a in trail2))
    check("audit newest-first ordering", trail2[0]["created_at"] >= trail2[-1]["created_at"])


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_simulation()
