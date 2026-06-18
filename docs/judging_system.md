# GSA Judging System — Admin & User Manual

> **Module:** `v2/core/judging/` · **Channel:** Telegram only (pilot)
> **Tests:** `python3 -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py v2/tests/test_judging_session.py -q` → 86 tests

---

## Overview

The judging system automates scoring for GSA events like 3MRP and Research Day.
Judges score presenters conversationally through Telegram. The audience can vote for their favourite presenter separately. The admin controls everything from the dashboard.

**Three independent flows, all via Telegram:**

| Who | Trigger | Purpose |
|---|---|---|
| Judge | `judge mode` | Score presenters criterion by criterion |
| Presenter | `presenter mode` | Register attendance (links Telegram to their number) |
| Anyone | `audience mode` | Cast one audience vote (optional, admin-activated) |

---

## Admin: Before the Event (Dashboard → Judging tab)

> Requires server mode. Open dashboard at `http://localhost:5555`.

### 1. Create the Event

Fill in the Create New Event form:

| Field | Description | Default |
|---|---|---|
| Event name | e.g. `3MRP 2026` | — |
| Top N winners | How many judge winners to rank (ties share rank) | 3 |
| Score min | Minimum score per criterion | 1 |
| Score max | Maximum score per criterion | 5 |
| Min judge coverage | Flag presenters scored by fewer than N judges (⚠️ in results) | 3 |
| Audience top N | How many audience winners to highlight | 1 |
| Criteria (one per line) | The scoring questions | 6 defaults below |

**Default criteria:**
```
Communication & Clarity
Research Content
Delivery & Engagement
Organization & Timing
Visual Slide Effectiveness
Overall Impression
```

Click **Create Event**. Status starts as `setup` — judges cannot log in yet.

### 2. Load Presenters

Paste a CSV in the Presenters section. Header is optional:

```
number,name,department
100,Jane Smith,Computer Science
101,Ali Hassan,Electrical Engineering
102,Maria Garcia,Biology
```

- `number` is the unique participant number — this is what judges and presenters type
- All presenters start **absent**. They become present by registering via Telegram or by you clicking **Mark Present**
- Unscored presenters score 0 and appear at the bottom of results

### 3. Add Judges

For each judge, enter their name and a PIN you choose. You distribute PINs privately (email, in person). The system links the PIN to their Telegram account the first time they log in.

**PIN rules:**
- Must be unique per event
- Once a judge logs in, that PIN is permanently linked to their Telegram account
- If a judge forgets their PIN: they can log back in with the same Telegram account and the same PIN, no re-entry needed (the bot recognises them automatically)

### 4. Open Judging

Click **Open**. Judges can now authenticate via Telegram. The event status changes to `open`.

### 5. Open Audience Voting (optional)

In the **Audience Voting** section of the manage panel, click **Open Audience Voting**. This can be done at any time while the event is open — before, during, or after judging. Anyone with Telegram can now vote.

---

## Judge Flow (Telegram)

### First Login

```
Judge:  judge mode
Bot:    Judge Mode — 3MRP 2026
        Please enter your judge PIN:

Judge:  J-001
Bot:    Authenticated as Amira Khalil.
        Judging is open for 3MRP 2026.
        Scores range: 1–5
        Say a participant number to start scoring.
        Say "my scores" to review what you've scored so far.
        Say "exit judge mode" at any time to return to normal mode.
```

### Returning (same Telegram account, same or different session)

```
Judge:  judge mode
Bot:    Welcome back, Amira Khalil! Judging is open for 3MRP 2026.
        Say a participant number to continue scoring.
        (No PIN asked — Telegram account already linked)
```

### Scoring a Presenter

```
Judge:  104
Bot:    Scoring #104 — Maria Garcia (Computer Science)

        Q1/6 — Communication & Clarity (1–5):
Judge:  4
Bot:    Q2/6 — Research Content (1–5):
Judge:  5
        ... (continues for each criterion) ...
Judge:  3
Bot:    Review your scores for #104 — Maria Garcia:
        Communication & Clarity: 4
        Research Content: 5
        Delivery & Engagement: 4
        Organization & Timing: 3
        Visual Slide Effectiveness: 5
        Overall Impression: 4

        Total: 25/30

        Type "yes" to submit or "redo" to start over.
Judge:  yes
Bot:    Score submitted for Participant #104.
        Say the next participant number to continue, or "my scores" to review.
```

**Total format:** `Total: X/Y` where `X` = sum of scores, `Y` = number of criteria × score_max. Always dynamic.

### Guard Rails

| Situation | Bot response |
|---|---|
| Non-numeric score | "Please enter a number from 1–5." + repeats question |
| Score out of range | "Score must be between 1–5." + repeats question |
| Unknown participant number | "Participant #999 not found. Check the number and try again." |
| Already scored (tries again) | Shows all previous scores + Total. "Contact the admin if you need a correction." |
| `redo` mid-scoring | Restarts from Q1 for the same presenter |
| `redo` at confirmation | Restarts from Q1 for the same presenter |

### Check Scoring History

```
Judge:  my scores
Bot:    Scores so far (12 presenter(s)):
          #100 Jane Smith — Total: 27/30
          #101 Ali Hassan — Total: 24/30
          ...
        Say a participant number to continue scoring.
```

### Exit

```
Judge:  exit judge mode
Bot:    You have exited. I'll answer GSA questions normally now.
```

---

## Presenter Flow (Telegram)

Presenters register so the system knows they showed up. All presenters are absent by default.

```
Presenter:  presenter mode
Bot:        Presenter Mode — 3MRP 2026
            Please enter your participant number:

Presenter:  100
Bot:        You are registered as #100 — Jane Smith, Computer Science.
            If there's a mistake, contact Mohammad immediately.
```

- After registering, the bot returns to normal — the presenter can ask GSA questions as usual
- Each participant number can only be claimed by one Telegram account
- If someone types the wrong number and it's taken: "already registered to a different account — contact Mohammad"
- Admin can also manually mark presenters present from the dashboard (no Telegram required)

---

## Audience Voting Flow (Telegram)

Available to anyone when audience voting is open. No PIN required.

### Casting a Vote

```
Person:  audience mode
Bot:     Audience Mode — 3MRP 2026
         Say a presenter number to cast your vote:

Person:  103
Bot:     You are voting for #103 — Carlos Reyes (Computer Science).
         Type "yes" to confirm or say a different number.

Person:  yes
Bot:     Vote cast for #103 — Carlos Reyes!
         Say "audience mode" again if you want to change your vote.
```

### Changing a Vote

Votes can be changed at any time while audience voting is open. The new vote replaces the old one.

```
Person:  audience mode
Bot:     Audience Mode — 3MRP 2026
         You previously voted for #103 — Carlos Reyes (Computer Science).
         Say a presenter number to change your vote, or "exit audience mode" to go back.

Person:  107
Bot:     You are voting for #107 — Priya Nair (Electrical Engineering).
         Type "yes" to confirm or say a different number.

Person:  yes
Bot:     Vote cast for #107 — Priya Nair!
```

### Judges Voting

Judges can say `audience mode` at any time from their `ready` state (between scorings — not mid-scoring). After voting, they are **automatically returned to Judge Mode**:

```
Bot:  Vote cast for #103 — Carlos Reyes!
      You're back in Judge Mode. Say a participant number to continue scoring.
```

---

## Admin: During the Event (Dashboard)

### Live Progress Panel (auto-refreshes every 10 seconds)

| Metric | What it means |
|---|---|
| Judges authenticated | How many judges have logged in via Telegram |
| Present presenters | How many presenters have registered (or been manually marked) |
| Scores submitted | Total score entries / max possible |
| Coverage % | Scores submitted ÷ (judges × presenters) |

### Presenter List

Shows each presenter with:
- ✅ **(Telegram)** — registered via Telegram
- ✅ **(Manual)** — you clicked Mark Present
- **Mark Present** button — for presenters who can't use Telegram
- **View** button — opens a drill-down showing every judge's per-criterion scores

### Drill-Down (click View on any presenter)

| Judge | Comm & Clarity | Research | ... | Avg | Submitted |
|---|---|---|---|---|---|
| Amira Khalil | 4 | 5 | ... | 4.17 | 2026-06-20 10:14 |
| David Chen | 5 | 4 | ... | 4.50 | 2026-06-20 10:22 |

Full bookkeeping: judge name, every criterion, average, timestamp.

### Delete a Score (Admin Fix)

Select a judge from the dropdown, enter the participant number, click **Delete Score**. The judge can then re-score that presenter via Telegram.

### Audience Voting Panel

Shows current status (🟢 Open / 🔴 Closed) and live vote results:
- Sorted by vote count descending
- 🏆 marks top-N audience winners
- Ties share rank
- Shows total vote count

---

## Admin: Closing the Event (Dashboard)

### Close Judging

Click **Close** → judges can no longer submit scores. Anyone who tries `judge mode` sees: *"Judging for 3MRP 2026 is now closed. Thank you!"*

### Close Audience Voting

In the Audience Voting panel, click **Close Audience Voting**. Anyone who tries `audience mode` sees: *"Audience voting for 3MRP 2026 is not active yet."*

### Results / Leaderboard

Click **Refresh Results**:

| Rank | # | Name | Department | Avg Score | Judges |
|---|---|---|---|---|---|
| 1 | 103 | Carlos Reyes | CS | 4.833 | 21 |
| 1 | 107 | Priya Nair | EE | 4.833 | 19 |
| 3 | 100 | Jane Smith | CS | 4.750 | 23 |

- Ties share rank — both rank 1 entries are co-winners
- ⚠️ flags presenters below min_coverage threshold (dimmed in results)
- Admin announces winners privately/manually — there is no "release results" button

### Export CSV

Click **Export CSV** — downloads a file with:
```
rank, number, name, department, avg_communication_and_clarity, avg_research_content,
avg_delivery_and_engagement, avg_organization_and_timing, avg_visual_slide_effectiveness,
avg_overall_impression, final_score, judge_count, low_coverage
```

---

## Three-State Event Messages

The bot gives appropriate messages depending on event state:

| Event state | `judge mode` response | `audience mode` response |
|---|---|---|
| `setup` (not opened yet) | "Judging for X has not opened yet. Please check back later." | "Audience voting for X has not opened yet." |
| `open` | Normal judging flow | Normal voting flow (if audience_voting = open) |
| `closed` | "Judging for X is now closed. Thank you!" | "X is now closed." |
| `open` but audience_voting = closed | — | "Audience voting for X is not active yet." |

---

## Scoring Summary

| Rule | Detail |
|---|---|
| Score display | `Total: X/Y` (X = sum, Y = criteria_count × score_max) |
| Leaderboard ranking | Average of all judges' per-criterion averages |
| Ties | Share rank — co-winners (no tiebreaker) |
| Absent/unscored | Score = 0, no rank |
| One score per judge per presenter | Bot blocks re-score; admin can delete to allow redo |
| Bot restart | In-progress (not yet submitted) scores lost; judge re-enters participant number |
| Audience: one vote per person | Replaces previous vote automatically |

---

## Database Tables

| Table | Purpose |
|---|---|
| `judging_events` | Event config (name, status, criteria JSON, score range, winners, coverage threshold, audience settings) |
| `judging_judges` | Judges per event (name, PIN, Telegram hash after first login) |
| `judging_presenters` | Presenters per event (number, name, dept, is_present, Telegram hash) |
| `judging_scores` | Scores (one row per judge × presenter, scores_json, final_score) |
| `judging_audience_votes` | Audience votes (one row per voter per event — upsert on change) |

All tables are SQLite STRICT. New columns are added via idempotent `_COLUMN_MIGRATIONS` — safe to run against an existing live database.

---

## HTTP API Reference

### GET endpoints

| Endpoint | Returns |
|---|---|
| `GET /judging/events` | All events |
| `GET /judging/events/<id>/status` | Progress + judges list |
| `GET /judging/events/<id>/results` | Leaderboard with low_coverage flags |
| `GET /judging/events/<id>/export` | CSV download |
| `GET /judging/events/<id>/judges` | Judge list |
| `GET /judging/events/<id>/presenters` | Presenter list with is_present |
| `GET /judging/events/<id>/presenters/<n>/scores` | Per-judge drill-down for presenter #n |
| `GET /judging/events/<id>/audience-results` | Audience vote counts + rankings |

### POST endpoints

| Endpoint | Action |
|---|---|
| `POST /judging/events` | Create event (name, criteria, top_n, score_min, score_max, min_coverage, audience_top_n) |
| `POST /judging/events/<id>/open` | Open judging |
| `POST /judging/events/<id>/close` | Close judging |
| `POST /judging/events/<id>/audience-open` | Open audience voting |
| `POST /judging/events/<id>/audience-close` | Close audience voting |
| `POST /judging/events/<id>/judges` | Add judge (name, pin) |
| `POST /judging/events/<id>/judges-delete` | Remove judge (judge_id) |
| `POST /judging/events/<id>/presenters` | Load presenter CSV |
| `POST /judging/events/<id>/present` | Admin mark presenter present (presenter_number) |
| `POST /judging/events/<id>/scores-delete` | Delete a score (judge_id, presenter_number) |
| `POST /judging/events/<id>/update` | Update event settings |

---

## Module Reference (`v2/core/judging/`)

| File | Purpose |
|---|---|
| `db.py` | All DB CRUD — events, judges, presenters, scores, audience votes |
| `session.py` | In-memory state machine for all three Telegram flows |
| `calculator.py` | Leaderboard (with ties + coverage flags), event progress, CSV export |

**Key `db.py` functions:**

```python
create_event(conn, name, criteria, top_n, score_min, score_max, min_coverage, audience_top_n)
get_open_event(conn)               # → event dict | None
get_any_event(conn)                # → most recent event, any status (three-state messages)
authenticate_judge(conn, event_id, pin, telegram_user_id)   # → judge dict | None
get_judge_by_telegram_hash(conn, event_id, telegram_user_id) # → resume check
register_presenter(conn, event_id, number, telegram_user_id) # → bool
mark_presenter_present(conn, event_id, number)               # admin manual
get_score(conn, event_id, judge_id, presenter_number)        # → already-scored display
get_all_scores_by_judge(conn, event_id, judge_id)            # → "my scores" list
get_presenter_scores_detail(conn, event_id, presenter_number) # → admin drill-down
cast_vote(conn, event_id, telegram_user_id, presenter_number) # upserts
get_vote(conn, event_id, telegram_user_id)                   # → current vote | None
get_audience_results(conn, event_id)                         # → ranked vote list
set_audience_voting(conn, event_id, status)                  # 'open' | 'closed'
```

---

## Wiring (how it connects to the bot)

- `bot/connectors/telegram_connector.py` — intercepts messages **before** the normal RAG handler. If `judging_manager.handle()` returns `consumed=True`, the message never reaches the GSA knowledge base.
- `run_telegram.py` — creates `JudgingSessionManager(db_path)` and passes it to the connector.
- Session state lives **in memory** — a bot restart clears in-progress (pre-submit) sessions but all committed scores survive in the DB.

---

## Checklist: Running 3MRP

**Day before:**
- [ ] Create event on dashboard with correct criteria and score range
- [ ] Load presenter CSV
- [ ] Add all judges with PINs, distribute PINs privately
- [ ] Verify presenter count matches your list

**Day of (before presentations start):**
- [ ] Click **Open** on the dashboard
- [ ] Ask judges to log in (`judge mode` → their PIN) — verify authentication count on dashboard
- [ ] Announce to presenters: DM the bot `presenter mode` → enter your number
- [ ] If audience voting: click **Open Audience Voting** when ready (can be during or after presentations)

**During:**
- [ ] Monitor Live Progress panel (auto-refreshes)
- [ ] Click **View** on any presenter for a real-time score breakdown
- [ ] Fix mistakes: select judge + participant number → **Delete Score** → judge can re-score

**After presentations:**
- [ ] Click **Close** (judging)
- [ ] Click **Close Audience Voting** (if active)
- [ ] Click **Refresh Results** — review leaderboard
- [ ] Note ⚠️ flagged rows (low judge coverage — use judgement before announcing)
- [ ] Click **Export CSV** for your records
- [ ] Announce winners yourself (privately or on stage)
