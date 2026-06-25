"""Event-driven World Cup state machine over ``NormMatch`` (pure, no I/O).

Scoreboard-primary model: goals carry a STABLE identity ``(match, athlete, minute)``, so
dedup and disallowed-goal correction are an id-diff, not a fragile score-walk:

  * new goal  = an identity we haven't announced  → emit "goal"
  * disallowed = an announced identity that vanished from a HEALTHY read (the read still
    carries other goals) → emit "correction". A transient/empty ``goals`` read changes
    nothing (never a false correction) — the "keep it simple" decision (2026-06-24).

Emitted events carry a football-data-shaped ``match`` adapter dict so the existing
``format_event`` / ``_dedup_key`` consume them unchanged, plus ``scorer``/``minute``/
``kind``/``team`` on goals to light up the dormant GOAL render branch.
"""
from __future__ import annotations

from v2.integration.wc_providers.normalize import NormMatch


def fresh_ledger() -> dict:
    return {"started": False, "finished": False,
            "announced_goals": set(), "score": (0, 0), "half": 1}


_HALF_LABEL = {1: "First Half", 2: "Second Half"}


def _uid(parts) -> str:
    """A compact, stable dedup token from a goal identity (or a set of them) — drives the
    enqueue dedup_key so a re-scored goal after a disallowance is never collapsed by score."""
    return "|".join(str(p) for p in parts)


def _half_label(period: int) -> str:
    return _HALF_LABEL.get(period, "Extra Time")


def _adapter(norm: NormMatch, score: tuple[int, int]) -> dict:
    """A football-data-shaped match dict built from NormMatch, so the existing formatters
    (``format_event`` / ``_score_line`` / ``_dedup_key`` / ``_context``) work unchanged."""
    return {
        "homeTeam": {"name": norm.home.name},
        "awayTeam": {"name": norm.away.name},
        "score": {"fullTime": {"home": score[0], "away": score[1]}},
        "stage": "", "group": norm.group or "",
        "utcDate": norm.utc_date,
    }


def _running_scores(norm: NormMatch):
    """Yield (goal, running_score) walking goals in order, incrementing the credited side
    (``team_id``) — correct for own goals too (ESPN credits ``team_id`` to the beneficiary)."""
    h = a = 0
    for g in norm.goals:
        if g.team_id == norm.away.id:
            a += 1
        else:
            h += 1
        yield g, (h, a)


def process_match(norm: NormMatch, ledger: dict, near_kickoff: bool = False) -> list[dict]:
    """Run the state machine for one match read; mutate ``ledger``; return events to post."""
    state = norm.state
    events: list[dict] = []

    if state == "done":
        if not ledger["finished"]:
            ledger["finished"] = True
            score = norm.score if norm.score != (0, 0) else ledger.get("score", (0, 0))
            events.append({"type": "fulltime", "match": _adapter(norm, score)})
        return events

    if state not in ("in_play", "paused", "shootout"):
        return events                                 # scheduled / unknown — ignore

    running = list(_running_scores(norm))             # [(goal, running_score), ...]
    identities = {g.identity for g, _ in running}

    if not ledger["started"]:
        ledger["started"] = True
        if norm.goals or norm.score != (0, 0):
            # caught after the opening whistle — adopt the current goals as a SILENT
            # baseline (never back-announce), announce kickoff only if still near kickoff.
            ledger["announced_goals"] |= identities
            ledger["score"] = norm.score
            if near_kickoff:
                events.append({"type": "kickoff", "match": _adapter(norm, norm.score)})
            return events
        events.append({"type": "kickoff", "match": _adapter(norm, (0, 0))})

    # disallowed-goal correction: an announced identity gone from a HEALTHY read (one that
    # still carries goals). An empty `goals` read is transient → never a false correction.
    if running:
        vanished = ledger["announced_goals"] - identities
        if vanished:
            ledger["announced_goals"] &= identities
            ledger["score"] = norm.score
            events.append({"type": "correction", "uid": _uid(sorted(map(str, vanished))),
                           "match": _adapter(norm, norm.score)})
            return events

    if state == "shootout":
        return events                                  # never walk shootout kicks as goals

    half = _half_label(1)
    for g, score in running:
        if g.identity in ledger["announced_goals"]:
            continue
        ledger["announced_goals"].add(g.identity)
        ledger["score"] = score
        team_name = norm.home.name if g.team_id != norm.away.id else norm.away.name
        events.append({
            "type": "goal", "scorer": g.scorer, "minute": g.minute, "kind": g.kind,
            "team": team_name, "half_label": half, "uid": _uid(g.identity),
            "scoring_team": {"name": team_name},
            "match": _adapter(norm, score)})
    return events
