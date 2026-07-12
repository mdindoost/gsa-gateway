"""ESPN scoreboard JSON → the provider-agnostic ``NormMatch`` shape (pure, no I/O).

Scoreboard-primary (design decision 2026-06-24): ONE shared scoreboard call carries every
live match, each with structured scorer names (``details[].athletesInvolved``), goal kind
(``ownGoal``/``penaltyKick``), and a per-play ``shootout`` flag — so a GOAL post gets
scorer+minute+kind with no prose parsing. Goal identity = ``(match_id, athlete_id, clock)``,
stable across reads (survives reordering, drives dedup + disallowed-goal correction).

Status is mapped from ``status.type`` (``state`` + ``completed`` + the in-play ``name``
enum). Mapping on ``state`` is robust vs football-data's IN_PLAY/LIVE ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TeamRef:
    id: int | None
    name: str
    abbreviation: str | None = None


@dataclass(frozen=True)
class GoalEvent:
    match_id: int
    team_id: int | None
    athlete_id: int | None
    scorer: str | None
    minute: str | None       # ESPN clock.displayValue, e.g. "29'"
    kind: str                # "goal" | "own_goal" | "penalty"
    seq: int = 0             # scoring-play index in the match — anon-goal tiebreaker only

    @property
    def identity(self):
        """Stable key for dedup/correction. ``athlete_id``+``minute`` is intrinsic to the
        goal, so it survives ``details[]`` reordering AND disallowances of OTHER goals. Only
        when the scorer is missing (rare) do we fall back to the positional ``seq`` so two
        anonymous same-minute goals don't collapse to one."""
        who = self.athlete_id if self.athlete_id is not None else f"seq{self.seq}"
        return (self.match_id, who, self.minute)


@dataclass
class NormMatch:
    id: int
    utc_date: str
    state: str | None        # canonical: in_play | paused | done | shootout | None
    home: TeamRef
    away: TeamRef
    score: tuple[int, int]
    minute: str | None = None
    goals: list[GoalEvent] = field(default_factory=list)
    group: str | None = None
    # Knockout finish metadata — only meaningful on a `done` read (canonicalization
    # collapses AET/PEN/regular finals all to `state == "done"`, so the distinction has to
    # be preserved here before it's lost). See _finish_meta.
    finish_kind: str | None = None       # "regulation" | "aet" | "penalties" | None
    shootout_score: tuple[int, int] | None = None   # (home, away); ONLY on a penalty final
    winner_side: str | None = None       # "home" | "away" | None (derived from shootout_score)


# in-play sub-states (status.type.name) that mean "a break" rather than active play
_PAUSED_NAMES = {"STATUS_HALFTIME", "STATUS_END_OF_PERIOD"}
# explicit penalty-shootout markers — score changes here are NOT goals
_SHOOTOUT_NAMES = {"STATUS_SHOOTOUT", "STATUS_PENALTIES", "STATUS_END_OF_REGULATION"}


def _canon_status(status_type: dict) -> str | None:
    """ESPN ``status.type`` → canonical state, or None if the watcher doesn't act on it."""
    state = status_type.get("state")
    name = status_type.get("name") or ""
    if state == "post" and status_type.get("completed"):
        return "done"
    if state == "in":
        if name in _SHOOTOUT_NAMES:
            return "shootout"
        if name in _PAUSED_NAMES:
            return "paused"
        return "in_play"
    return None   # pre / postponed / abandoned / suspended / unknown → ignored


# Completed-status names that carry knockout finish info; anything else DONE is regulation.
_FINAL_NAMES = {"STATUS_FINAL_PEN": "penalties", "STATUS_FINAL_AET": "aet"}


def _finish_meta(status_type: dict, home: dict, away: dict):
    """For a DONE read, derive ``(finish_kind, shootout_score, winner_side)``.

    ``finish_kind`` distinguishes penalties / AET / regulation — a distinction _canon_status
    collapses to "done", so it must be recovered from the raw status name here. ``shootout_score``
    is captured ONLY for a penalty final and ONLY when BOTH sides parse (a missing pen score is
    never coerced to 0). ``winner_side`` is derived from the shootout score (the literal result);
    a tie or absent score yields None rather than a guessed winner."""
    kind = _FINAL_NAMES.get(status_type.get("name") or "", "regulation")
    shootout = winner = None
    if kind == "penalties":
        so_h, so_a = _to_int(home.get("shootoutScore")), _to_int(away.get("shootoutScore"))
        if so_h is not None and so_a is not None:
            shootout = (so_h, so_a)
            if so_h != so_a:
                winner = "home" if so_h > so_a else "away"
    return kind, shootout, winner


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _team_ref(competitor: dict) -> TeamRef:
    t = competitor.get("team") or {}
    return TeamRef(id=_to_int(t.get("id")),
                   name=t.get("displayName") or t.get("name") or "",
                   abbreviation=t.get("abbreviation"))


def _goal_kind(detail: dict) -> str:
    if detail.get("ownGoal"):
        return "own_goal"
    if detail.get("penaltyKick"):
        return "penalty"
    return "goal"


def _goals(match_id: int, details: list) -> list[GoalEvent]:
    out: list[GoalEvent] = []
    for idx, d in enumerate(details or []):
        if not d.get("scoringPlay") or d.get("shootout"):
            continue
        ath = (d.get("athletesInvolved") or [{}])[0]
        out.append(GoalEvent(
            match_id=match_id,
            team_id=_to_int((d.get("team") or {}).get("id")),
            athlete_id=_to_int(ath.get("id")),
            scorer=ath.get("displayName"),
            minute=(d.get("clock") or {}).get("displayValue"),
            kind=_goal_kind(d),
            seq=idx))
    return out


def event_to_match(event: dict) -> NormMatch:
    """One ESPN scoreboard ``events[]`` entry → NormMatch."""
    comp = (event.get("competitions") or [{}])[0]
    status = comp.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    state = _canon_status(status_type)
    comps = comp.get("competitors") or []
    home = next((c for c in comps if c.get("homeAway") == "home"), {})
    away = next((c for c in comps if c.get("homeAway") == "away"), {})
    match_id = _to_int(event.get("id"))
    finish_kind = shootout_score = winner_side = None
    if state == "done":
        finish_kind, shootout_score, winner_side = _finish_meta(status_type, home, away)
    return NormMatch(
        id=match_id,
        utc_date=event.get("date") or "",
        state=state,
        home=_team_ref(home),
        away=_team_ref(away),
        score=(_to_int(home.get("score")) or 0, _to_int(away.get("score")) or 0),
        minute=(status.get("displayClock")),
        goals=_goals(match_id, comp.get("details")),
        finish_kind=finish_kind, shootout_score=shootout_score, winner_side=winner_side)


def scoreboard_to_matches(payload: dict) -> list[NormMatch]:
    """Whole ESPN scoreboard payload → list of NormMatch (one per event)."""
    return [event_to_match(e) for e in (payload.get("events") or [])]
