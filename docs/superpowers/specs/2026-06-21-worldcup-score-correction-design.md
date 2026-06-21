# World Cup — Handle disallowed-goal / score corrections (VAR)

> Status: DESIGN (pending senior-eng review + Mohammad approval). 2026-06-21.
> Scope: `v2/integration/match_watcher.py` only (plus tests). No schema, no other modules.

## The bug (observed, with evidence)

Belgium v Iran, match `537365`, 2026-06-21 (from `logs/wc_api_debug.log`):

```
19:06  IN_PLAY 0-0            kickoff
19:31  IN_PLAY 0-1            real on-field goal (Iran). We POSTED the goal. ledger score=(0,1)
 ...   IN_PLAY 0-1            held ~28 min
19:59  PAUSED  0-0  lastUpdated=19:59:25   VAR disallowed the goal; API corrects DOWN to 0-0
 ...   PAUSED/IN_PLAY 0-0     stays 0-0 the rest of the match
21:06  FINISHED 0-0  lastUpdated=21:06:37  real final = 0-0
```

Resulting ledger: `"537365": { "score": [0,1], "finished": true }` — **wrong**. We posted a goal
that was overturned, never posted a correction, and the full-time message went out as 0-1.

## Root cause

`match_watcher.py:239`:

```python
nh, na = max(h, ph), max(a, pa)   # monotonic — never go down
```

The ledger score is **monotonic**. That guard exists to stop a *stale/empty* read (junk `0-0`)
from erasing a real `1-0`, and for that case it is correct. But it **conflates** two cases:

1. **Stale/empty read** showing a lower score → keep old score ✓ (designed for this)
2. **Fresh read with a genuinely lower score** (VAR / disallowed goal) → should correct down,
   but `max()` silently keeps the old, now-wrong score ✗ (never handled)

`_process` line 204 applies `max` again at FINISHED, so the wrong score also propagates into the
full-time post.

**Why "the code is correct" was wrong before:** it was correct only for case (1). The retraction
path (case 2) was never implemented at all.

## The discriminator (already available, currently unused)

The API read carries `lastUpdated`. A genuine correction is a read that is **fresh** (its
`lastUpdated` is strictly newer than the `lastUpdated` at which we recorded the current score) AND
is a live read (`status in LIVE`) AND shows a strictly lower score. A stale snapshot has an **old**
`lastUpdated`, so it stays under monotonic protection.

Key asymmetry that keeps the change small and safe:
- **Increases** stay as-is (apply immediately). A stale/old read can only show an *old, lower*
  score — it can never invent a *higher* one — so increases never need the freshness gate.
- **Only decreases** get the new freshness-gated correction path.

## Design

### Ledger: add one field
`_fresh_ledger` / `_normalize` gain `"score_updated": None` — the `lastUpdated` string of the read
that set the current score. Backward-compatible (older files default to `None`).

`_parse` must stop flattening `None`→`0` in a way that hides emptiness. Add a helper that returns
the raw `lastUpdated` and whether the read actually carried a score (home/away not both `None`), so
an empty payload is never treated as a real `0-0`.

### `_process` — new decrease branch (LIVE only)
After computing the incoming `(h,a)` for a LIVE read:

```
if read is fresh (read.lastUpdated > state["score_updated"]) and read carried a real score
   and (h,a) < state["score"] componentwise-or-either-side-lower:
       -> emit a "correction" event with the new scoreline
       -> state["score"] = (h,a); state["score_updated"] = read.lastUpdated
else:
       -> existing monotonic increase logic (and set score_updated when score moves up)
```

Decrease handling walks **down** to mirror the up-walk: if 0-2 → 0-1 (one of two goals
disallowed), emit one correction to 0-1; if 0-2 → 0-0, the post states the new line 0-0. (Open
question for reviewer: one correction post showing the final corrected line, vs one per removed
goal. Leaning: a single correction post per fresh read — disallowed goals are rare and one clear
"score corrected to X" is less noisy.)

### New event type `"correction"`
`format_event`: e.g. `⚠️ **Score correction** (VAR)\n{_score_line(match)}` — wording TBD with
Mohammad. `_dedup_key`: `f"{match_id}:correction:{home}-{away}"` so re-reads of the same corrected
line don't double-post, and a later real goal re-uses the normal goal key.

### FINISHED reconcile
Once the live decrease path corrects the score during play, by the time FINISHED arrives
`state["score"]` is already `(0,0)`, so the existing `max` at line 204 yields the correct
full-time. The `max` at FINISHED is **kept** as the safety net for the documented "only saw 1-0
live but ended 4-1" case (FINISHED revealing a higher final). No change needed there — but the
reviewer should confirm this reasoning holds when a correction happens in the *same* read that goes
FINISHED (rare; FINISHED branch returns before the LIVE branch).

## Out of scope / non-goals
- No change to polling cadence, key budget, or the stale-read monotonic protection for increases.
- No retroactive edit/delete of the already-posted goal message (we append a correction, we don't
  redact — matches how broadcasters announce "goal disallowed"). Reviewer: confirm append-only is
  acceptable vs. editing the prior Discord/Telegram message.

## Test plan (TDD, replay-driven)
1. **Failing test first**: feed the exact Belgium-v-Iran read sequence (0-0 → 0-1 → PAUSED 0-0
   fresh → FINISHED 0-0) through `_process`; assert events = [goal 0-1, correction 0-0, fulltime
   0-0] and final ledger score = (0,0). This fails today (gives fulltime 0-1).
2. Stale-protection regression: real 0-1, then a *stale* 0-0 (old `lastUpdated`) → NO correction,
   score stays (0,1). Guards against over-correcting on junk reads.
3. Partial retraction: 0-2 → fresh 0-1 → one correction to 0-1.
4. Existing `test_match_watcher.py` suite stays green EXCEPT the 3 exact-dict persistence tests,
   which are updated for the new `score_updated` key (monotonic increase, kickoff grace, FINISHED
   higher-final reconcile, dedup keys all still asserted).

Added per review:
5. **Empty-payload-does-not-correct:** real 0-1, then `IN_PLAY {None,None}` with a NEWER
   `lastUpdated` → NO correction (the dangerous case the `carried_score` gate guards).
6. **Equal `lastUpdated`, lower score** → NO correction (boundary: must be strictly newer).
7. **`score_updated is None`** (first decrease / resumed old ledger) → NO correction.
8. **Catch-late → FINISHED-lower:** replay 537365 with the PAUSED reads removed → assert full-time
   = 0-0 (exercises the freshness-aware FINISHED reconcile).
9. **Re-score-after-correction:** 0-1 → corrected 0-0 → 0-1 again → second 0-1 is NOT dropped.
10. **Persistence round-trip** of `score_updated` through save/load (and resume-mid-correction).

## Revisions after senior-eng review (2026-06-21) — all adopted

1. **Empty-payload gate is load-bearing (must-fix #1).** The decrease branch fires ONLY when the
   read carried a real score (`home` AND `away` both non-None) — never on an empty payload, even
   if its `lastUpdated` advanced. `_parse` keeps its current signature (so `test_parse_nulls_to_zero`
   stays valid); a NEW helper returns `(lastUpdated, carried_score)` for the freshness/decrease gate.
   Full decrease condition: `status in LIVE` AND `carried_score` AND `lastUpdated > score_updated`
   AND new score strictly lower on either side.
2. **Persist + normalize `score_updated`, and update the affected tests (must-fix #2).** Adding the
   field WILL break the exact-dict assertions in `test_save_then_load_roundtrips_ledger`,
   `test_match_state_returns_fresh_for_unknown_match`, `test_load_normalizes_missing_half_keys` —
   these are EXPECTED to change, not regressions. `score_updated` defaults to `None` for old files.
   With `score_updated is None` (increase-only baseline, or resumed pre-upgrade ledger) a decrease
   does NOT fire — documented behavior.
3. **Branch placement (must-fix #3).** The decrease check is an explicit early branch that runs
   INSTEAD OF the monotonic walk-up — it must not fall through into the increase walk. Half-tracking
   (PAUSED→pending_half) still runs first; the real correction lands on a PAUSED read, which is fine.
4. **FINISHED reconcile must be freshness-aware too (must-fix #4 — closes the catch-late residual
   of the reported bug).** If we MISS the live PAUSED 0-0 reads and only catch `FINISHED 0-0`, the
   old `max(0,1)=1` still posts full-time 0-1 — the exact bug, uncorrected. Fix:
   ```
   if finished read carried_score and read.lastUpdated > state["score_updated"]:
       final = (h, a)                      # trust FINISHED, up OR down
   else:
       final = (max(h, ph), max(a, pa))    # safety net: empty/stale FINISHED keeps tracked score
   ```
   This still handles the documented "only saw 1-0 live, ended 4-1" case (fresh higher final → trust),
   and the "empty FINISHED payload" case (not carried → keep tracked). When a correction was caught
   during play, full-time also reflects the corrected score, so a fresh-FINISHED is allowed to emit a
   correction if it's the first place we see the drop.
5. **Re-score-after-correction dedup (should-consider, adopted).** A goal disallowed (0-1→0-0) then
   re-scored (0-0→0-1) would collide on `match:goal:0-1` and be dropped. Add a per-match correction
   generation counter to the goal dedup key (`match:goal:{gen}:{home}-{away}`) so a re-scored line
   posts. `correction` key stays `match:correction:{home}-{away}`.

Open questions — resolved by reviewer, locked in: **(Q1)** ONE correction post per fresh decrease
read stating the new line (no goal-by-goal walk-down). **(Q2)** APPEND-only — the post pipeline
(`enqueue_post`) is fire-and-forget with no message-id retention / edit API, so editing the prior
message is out of scope and worse UX anyway.

## Owner adjustments at build time (2026-06-21)
6. **Wording:** message is `⚠️ Score correction` — the "(VAR)" qualifier was dropped (we can't
   always know the *reason* for the drop; the score change is what we report).
7. **`correction_gen` is also in the correction dedup key** (`<id>:correction:<gen>:H-A`), not just
   the goal key. Closes a 2nd-correction-onto-the-same-line collision (0-0 → goal → disallowed →
   0-0 again would otherwise reuse `correction:0-0` and the 2nd post would be deduped/dropped).
8. **Verified "resume after correction"** (owner's explicit concern): after a correction the running
   score continues from the corrected baseline for goals on EITHER side, with non-colliding keys
   (`goal:<gen>:H-A`). Covered by `test_goals_from_both_sides_resume_correctly_after_correction`.

## Goals checklist (to verify at review + before "done")
- [ ] Fresh-vs-stale discriminator implemented via `lastUpdated` (not minute/score-only)
- [ ] Downward correction updates ledger AND posts a correction event
- [ ] Stale/empty reads still cannot lower the score (monotonic protection intact)
- [ ] Full-time reflects the corrected score (0-0 for this match)
- [ ] FINISHED higher-final reconcile still works (no regression)
- [ ] Replay test of the real log sequence passes; full suite green
