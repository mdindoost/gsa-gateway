# 3-Minute Research Presentation — Judging System Design

## Overview

Discord-based digital scoring system to replace paper forms + manual Excel entry.
~100 presenters, 20–25 judges, 5 criteria each scored 1–5.

---

## Roles & Permissions

| Who | Role | Can do |
|---|---|---|
| Admin (Mohammad) | `ADMIN_ROLE_NAME` | All `/judging` commands |
| Judges | `3MRP Judge` | `/score` only |
| Everyone else | — | Nothing — silent ephemeral block |

---

## Judging Channel

### Setup (done once in Discord, not the bot)
1. Create a channel named `#3mrp-judging`
2. Set channel permissions: only `3MRP Judge` role can view and send messages
3. Regular members cannot see the channel at all

### Why a dedicated channel
- Keeps judging activity separate from general server traffic
- All `/score` interactions are **ephemeral** — Discord guarantees only the person who ran the command sees the response
- Even with 25 judges in the same channel simultaneously, no judge sees another judge's scores, confirmations, or errors
- Clean audit trail — all judging activity happens in one place

### What it looks like during the event
```
#3mrp-judging  (visible only to judges + admin)

Judge A sees:  ✓ Score submitted — #102 Maria Garcia...
Judge B sees:  ✓ Score submitted — #105 Ali Hassan...
Judge C sees:  (their own messages only)
```
The channel appears empty to each judge except for their own ephemeral interactions.

---

## CSV Format (fixed — admin provides before event)

```
number,name,department
100,Jane Smith,Computer Science
101,Ali Hassan,Electrical Engineering
102,Maria Garcia,Biology
```

Rules:
- `number` — any integer, assigned by admin, must be unique
- `name` — full name, no special characters
- `department` — free text
- Header must be exactly: `number,name,department`

---

## Admin Commands

| Command | What it does |
|---|---|
| `/judging load` | Upload CSV — loads all presenters into SQLite, confirms count |
| `/judging open` | Scoring goes live — judges can now use `/score` |
| `/judging status` | Shows presenters loaded, scores submitted, which judges have scored |
| `/judging fix number:102 judge:@name` | Deletes a judge's score for a presenter so they can re-score |
| `/judging close` | Locks scoring — `/score` blocked for everyone |
| `/judging results` | Live leaderboard top 10 (ephemeral — only admin sees) |
| `/judging export` | Sends final CSV file as attachment in Discord |
| `/judging reset` | Wipes all presenters and scores — for next year's event |

All admin responses are ephemeral.

---

## Judge Flow

### Step 1 — Judge types
```
/score number: 102
```

### Step 2 — Bot replies ephemerally (only judge sees)
```
┌──────────────────────────────────────┐
│  Presenter #102                      │
│  Maria Garcia                        │
│  Biology                             │
│                                      │
│  [ ✓ Score This Presenter ]  [ ✗ Cancel ] │
└──────────────────────────────────────┘
```

### Step 3 — Judge clicks Score → modal opens
```
┌─────────────────────────────────────┐
│  Scoring #102 — Maria Garcia        │
│  ─────────────────────────────────  │
│  Communication & Clarity  (1–5): [ ]│
│  Research Content         (1–5): [ ]│
│  Delivery & Engagement    (1–5): [ ]│
│  Organization & Timing    (1–5): [ ]│
│  Visual Slide             (1–5): [ ]│
│  Overall Impression       (1–5): [ ]│
│                              [Submit]│
└─────────────────────────────────────┘
```

### Step 4 — Confirmation (ephemeral)
```
✓ Score submitted — #102 Maria Garcia
  Comm: 4 · Content: 5 · Delivery: 4 · Org: 3 · Visual: 5 · Overall: 4
  Average: 4.17
```

---

## Judging Criteria

| # | Criterion | Description | Scale |
|---|---|---|---|
| 1 | Communication & Clarity | Clear, well-articulated, accessible to non-specialist | 1–5 |
| 2 | Research Content | Problem, methodology, results, significance clearly explained | 1–5 |
| 3 | Delivery & Engagement | Confidence, enthusiasm, voice, pacing, body language | 1–5 |
| 4 | Organization & Timing | Logical structure, fits within 3-minute constraint | 1–5 |
| 5 | Visual Slide Effectiveness | Clear, readable, supports spoken content without clutter | 1–5 |
| 6 | Overall Impression | Holistic quality — would you remember this presentation tomorrow? | 1–5 |

---

## Guard Rails

| Situation | Bot response |
|---|---|
| Wrong presenter number | `#999 not found. Check your presenter list.` |
| Already scored that presenter | `You already scored #102. Contact admin if this was a mistake.` |
| Scoring not open yet | `Scoring is not open yet.` |
| Scoring closed | `Scoring has closed.` |
| Score outside 1–5 | `Scores must be between 1 and 5.` |
| No `3MRP Judge` role | `You are not registered as a judge for this event.` |

All errors are ephemeral — only the judge sees them. Judges cannot see each other's scores.

---

## Scoring Calculation

```
Per-judge score for a presenter = (Communication + Content + Delivery + Organization + Visual) / 5
Final presenter score            = mean of all judges' per-judge scores
```

- Ties → both announced as co-winners, no tiebreaker
- Judges cannot revise a submitted score (admin can delete + allow re-score via `/judging fix`)

---

## Export CSV Format

```
rank,number,name,department,avg_communication,avg_content,avg_delivery,avg_organization,avg_visual,avg_overall_impression,final_score,judges_count
1,102,Maria Garcia,Biology,4.2,4.6,4.1,4.4,4.3,4.5,4.35,22
2,100,Jane Smith,Computer Science,4.0,4.5,4.3,4.1,4.2,4.1,4.20,23
```

---

## Database Tables

### `judging_presenters`
| Column | Type | Notes |
|---|---|---|
| number | INTEGER PRIMARY KEY | Admin-assigned presenter number |
| name | TEXT | Full name |
| department | TEXT | Department or program |

### `judging_scores`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto |
| presenter_number | INTEGER | FK → judging_presenters.number |
| judge_id_hash | TEXT | Hashed — same privacy model as rest of bot |
| communication | INTEGER | 1–5 |
| content | INTEGER | 1–5 |
| delivery | INTEGER | 1–5 |
| organization | INTEGER | 1–5 |
| visual | INTEGER | 1–5 |
| overall_impression | INTEGER | 1–5 |
| submitted_at | TEXT | ISO timestamp |

---

## Judge Report (after close)

### When
Only available after `/judging close`. Admin triggers with `/judging report` which
DMs every judge their personal report automatically.

### What judges see (ephemeral DM)
```
📊 Your Judging Report — 3MRP 2026
You scored 24 presenters

                     You    Mean   Median   Std Dev   Min   Max
Communication        3.8    4.1     4.2      0.6       1     5
Research Content     4.2    4.0     4.0      0.5       2     5
Delivery             3.6    3.9     4.0      0.7       1     5
Organization         4.0    4.1     4.1      0.4       2     5
Visual Slide         4.3    4.2     4.0      0.5       2     5
Overall Impression   4.1    4.0     4.0      0.6       1     5
──────────────────────────────────────────────────────────────
Overall              3.98   4.06    4.06     0.4       1     5

Your scores vs group:  -0.08 below group average (lean slightly harsh)
Scoring range you used:  1 (lowest given) – 5 (highest given)
You scored slightly below the group average on Communication
and Delivery — the group leaned higher on those two criteria.
```

### What it does NOT show
- Other judges' individual scores — aggregated group stats only
- Which presenters anyone scored
- Any ranking of judges against each other

---

## Presenter Report (after winners announced)

### Registration — Option A (confirmed)
Before the event, admin announces in the server:
> "If you're presenting, run `/present claim number: 102` to receive your results after the event."

Bot links their Discord ID to their presenter number in SQLite.
After admin runs `/judging release`, bot DMs every registered presenter their report.

Guard rails for `/present claim`:
- Number must exist in `judging_presenters`
- Number not already claimed by another Discord account
- Only available while event is registered (before `/judging close`)

### When
Admin runs `/judging release` **after** the winners have been publicly announced.
This preserves the surprise of the ceremony — no presenter sees their rank early.

### What presenters see (DM)
```
📊 Your Results — 3MRP 2026
Maria Garcia · #102 · Biology
Scored by 22 judges

                     You    Winner   Field Mean   Median   Std Dev   Min   Max
Communication        4.2     4.8        4.1         4.2      0.6      3     5
Research Content     4.6     4.9        4.0         4.0      0.5      3     5
Delivery             4.1     4.7        3.9         4.0      0.7      2     5
Organization         4.4     4.8        4.1         4.1      0.4      3     5
Visual Slide         4.3     4.6        4.2         4.0      0.5      3     5
Overall Impression   4.5     4.9        4.0         4.0      0.6      3     5
──────────────────────────────────────────────────────────────────────────────
Overall              4.35    4.76       4.06        4.06     0.4      2     5

Rank:       3rd out of 98 presenters
Percentile: Top 3%

Strongest criterion:  Research Content (0.6 above field average)
Area to develop:      Delivery (0.2 below field average)
```

### What it does NOT show
- Other presenters' individual scores — winner stats and field aggregates only
- Any judge identities or individual judge scores

---

## Updated Admin Command List

| Command | What it does |
|---|---|
| `/judging load` | Upload CSV — loads all presenters into SQLite, confirms count |
| `/judging open` | Scoring goes live — judges can now use `/score` |
| `/judging status` | Shows presenters loaded, scores submitted, which judges have scored |
| `/judging fix number:102 judge:@name` | Deletes a judge's score for a presenter so they can re-score |
| `/judging close` | Locks scoring — `/score` blocked for everyone |
| `/judging report` | DMs every judge their personal scoring report |
| `/judging results` | Live leaderboard top 10 (ephemeral — only admin sees) |
| `/judging export` | Sends final CSV file as attachment in Discord |
| `/judging release` | DMs every registered presenter their personal results report — run after winners announced publicly |
| `/judging reset` | Wipes all presenters, scores, and claims — for next year's event |

---

## Updated Database Tables

### `judging_presenters`
| Column | Type | Notes |
|---|---|---|
| number | INTEGER PRIMARY KEY | Admin-assigned presenter number |
| name | TEXT | Full name |
| department | TEXT | Department or program |
| discord_id_hash | TEXT | Hashed Discord ID — set when presenter runs `/present claim` |

### `judging_scores`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto |
| presenter_number | INTEGER | FK → judging_presenters.number |
| judge_id_hash | TEXT | Hashed — same privacy model as rest of bot |
| communication | INTEGER | 1–5 |
| content | INTEGER | 1–5 |
| delivery | INTEGER | 1–5 |
| organization | INTEGER | 1–5 |
| visual | INTEGER | 1–5 |
| overall_impression | INTEGER | 1–5 |
| submitted_at | TEXT | ISO timestamp |

### `judging_votes`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto |
| voter_id_hash | TEXT | Hashed Discord ID — one row per person |
| presenter_number | INTEGER | FK → judging_presenters.number |
| submitted_at | TEXT | ISO timestamp |

Unique constraint on `voter_id_hash` — enforces one vote per person at DB level.

### `judging_state`
| Column | Type | Notes |
|---|---|---|
| key | TEXT PRIMARY KEY | e.g. `status`, `vote_status`, `released` |
| value | TEXT | e.g. `open`, `closed`, `true` |

Persists scoring and voting state across bot restarts.

---

## New Files (implementation)

```
bot/commands/judging.py    — all judging + /score + /present claim + modals
```

No other existing files touched except:
- `bot/main.py` — one line to register the new cog
- `bot/services/database.py` — new table creation methods

---

## Open Questions (to resolve before build)

### 1. Multiple rooms / tracks — RESOLVED
Rooms don't matter for scoring. Judges enter the number of whoever is in front of them.
Only matters if per-track winners needed — if one global ranking, ignore rooms entirely.
→ **Decision needed: one global winner or per-track winners?**

### 2. How many presenters does each judge score?
All 100 or only those in their assigned room?
→ **Decision needed.**

### 3. Winners structure — RESOLVED
- **Judges:** 3 winners (1st, 2nd, 3rd place) from judging scores
- **Audience:** 1 winner by popular vote, announced separately from judge winners

#### Audience voting — confirmed design
- Command: `/vote number: 102` — one command, works for all 100 presenters
- **Who can vote:** everyone in the server including presenters
- **Privacy:** all vote interactions are ephemeral — no one can see who anyone else voted for, vote counts are hidden until admin reveals
- **One vote per person:** enforced by bot, cannot be changed after submission
- Admin controls: `/judging vote-open` → `/judging vote-close` → `/judging vote-results`

#### Audience voting flow
```
Admin:   /judging vote-open
→ Bot announces in server: "🗳️ Audience Choice voting is now open!
   /vote number: 102  ·  One vote per person  ·  Closes at 8:00 PM"

Member:  /vote number: 102
→ ✓ Your vote has been cast for #102 — Maria Garcia
  (ephemeral — only you see this)

Admin:   /judging vote-close
Admin:   /judging vote-results   ← ephemeral, only admin sees before announcement
```

#### Guard rails
| Situation | Bot response |
|---|---|
| Vote twice | `You already voted. Votes cannot be changed.` |
| Wrong number | `#999 not found. Check the presenter list.` |
| Voting not open | `Audience voting is not open yet.` |
| Voting closed | `Audience voting has closed.` |

All responses ephemeral — vote counts and individual votes never visible to anyone except admin via `/judging vote-results`.

### 4. No-show presenters
Add `/judging skip number:103` to mark absent and exclude from results?
→ **Decision needed.**

### 5. Minimum judge coverage
Flag presenters scored by fewer than N judges in the export?
→ **Decision needed.**

### 6. Judge score history during event
`/score history` so judges can review their submissions while scoring is open?
→ **Decision needed.**

### 7. Recovery transparency for judges
Read-only `/judging status` visible to judges showing scoring is open and their count?
→ **Decision needed.**

---

## Resolved Decisions

| # | Decision | Answer |
|---|---|---|
| Rooms affect scoring? | No — judges enter number of whoever is in front of them | Resolved |
| Judge report timing | Only after `/judging close` | Resolved |
| Presenter report timing | After admin runs `/judging release` (post-announcement) | Resolved |
| Presenter report content | Rank + percentile + per-criterion vs winner + field stats | Resolved |
| Presenter registration | Option A — `/present claim number: 102` | Resolved |
| Ties | Both announced as co-winners, no tiebreaker | Resolved |
| Score revisions | One score only — admin can delete via `/judging fix` | Resolved |
| Score privacy | All responses ephemeral — judges never see each other's scores | Resolved |
| Judging channel | `#3mrp-judging` — visible to `3MRP Judge` role only | Resolved |

---

## Status

> Core flow confirmed. 7 open questions remain.
> **Not yet implemented** — awaiting answers before build.
