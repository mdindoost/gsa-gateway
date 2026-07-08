"""Central configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _ops_path_default(database_path: str) -> str:
    """Derive the default OPS DB path as a sibling of database_path.

    Swaps the filename of database_path to ``gsa_gateway_ops.db``.
    E.g. ``./gsa_gateway.db`` → ``./gsa_gateway_ops.db``.
    """
    p = Path(database_path)
    return str(p.parent / "gsa_gateway_ops.db")


@dataclass
class Config:
    """Typed configuration object for the GSA Gateway bot."""

    discord_token: str
    discord_guild_id: int | None
    admin_role_name: str
    database_path: str
    operations_db_path: str
    ollama_enabled: bool
    ollama_model: str
    ollama_url: str
    ollama_timeout: int
    log_level: str
    allowed_channels: list[str]
    bot_prefix: str
    data_dir: Path
    # Announcement channel names (must match Discord channel names exactly)
    channel_announcements: str
    channel_events: str
    channel_food: str
    channel_funding: str
    channel_wellness: str
    channel_research: str
    channel_international: str
    # Scheduler settings
    daily_digest_hour: int
    daily_digest_minute: int
    reminder_check_interval: int
    # RAG / vector store settings
    chroma_db_path: str
    conversation_timeout_minutes: int
    conversation_max_turns: int
    embedding_model: str
    ask_gsa_channel: str
    # MathCafe
    mathcafe_channel: str
    mathcafe_enabled: bool
    # Admin notification
    admin_discord_id: int | None
    # Telegram
    telegram_token: str
    telegram_enabled: bool
    # Football / World Cup
    football_api_key: str
    football_enabled: bool
    football_channel: str
    football_poll_interval: int
    # Telegram channel broadcasting (in addition to existing DM connector)
    telegram_channel_id: str
    telegram_chat_id: str
    telegram_broadcast_target: str  # chat_id preferred, channel_id as fallback
    # GroupMe — outbound needs only the bot_id; inbound uses polling (access token).
    # Runs as its own process (run_groupme.py), mirroring the Telegram connector.
    groupme_enabled: bool
    groupme_bot_id: str
    groupme_access_token: str
    groupme_group_id: str
    groupme_poll_interval: int
    # Dashboard control plane — bot supervises v2/local_server.py as a child so the
    # localhost dashboard backend (and its /api/* job runner) is always-on for free.
    dashboard_server_enabled: bool
    dashboard_server_port: int


def load_config() -> Config:
    """Read environment variables and return a validated Config object."""
    raw_guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    guild_id = int(raw_guild) if raw_guild else None

    raw_channels = os.getenv("ALLOWED_CHANNELS", "").strip()
    allowed = [ch.strip() for ch in raw_channels.split(",") if ch.strip()]

    db_path = os.getenv("DATABASE_PATH", "./gsa_gateway.db")
    return Config(
        discord_token=os.getenv("DISCORD_TOKEN", ""),
        discord_guild_id=guild_id,
        admin_role_name=os.getenv("ADMIN_ROLE_NAME", "GSA Officer"),
        database_path=db_path,
        operations_db_path=os.getenv("OPERATIONS_DB_PATH", _ops_path_default(db_path)),
        ollama_enabled=os.getenv("OLLAMA_ENABLED", "false").lower() == "true",
        ollama_model=os.getenv("OLLAMA_MODEL", "granite4:tiny-h"),
        ollama_url=os.getenv("OLLAMA_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")),
        ollama_timeout=int(os.getenv("OLLAMA_TIMEOUT", "60")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        allowed_channels=allowed,
        bot_prefix=os.getenv("BOT_PREFIX", "gsa"),
        data_dir=Path(__file__).parent / "data",
        channel_announcements=os.getenv("CHANNEL_ANNOUNCEMENTS", "gsa-announcements"),
        channel_events=os.getenv("CHANNEL_EVENTS", "gsa-events"),
        channel_food=os.getenv("CHANNEL_FOOD", "gsa-food"),
        channel_funding=os.getenv("CHANNEL_FUNDING", "gsa-funding"),
        channel_wellness=os.getenv("CHANNEL_WELLNESS", "gsa-wellness"),
        channel_research=os.getenv("CHANNEL_RESEARCH", "gsa-research"),
        channel_international=os.getenv("CHANNEL_INTERNATIONAL", "gsa-international"),
        daily_digest_hour=int(os.getenv("DAILY_DIGEST_HOUR", "9")),
        daily_digest_minute=int(os.getenv("DAILY_DIGEST_MINUTE", "0")),
        reminder_check_interval=int(os.getenv("REMINDER_CHECK_INTERVAL", "30")),
        chroma_db_path=os.getenv("CHROMA_DB_PATH", "./chroma_db"),
        conversation_timeout_minutes=int(os.getenv("CONVERSATION_TIMEOUT_MINUTES", "60")),
        conversation_max_turns=int(os.getenv("CONVERSATION_MAX_TURNS", "5")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        ask_gsa_channel=os.getenv("ASK_GSA_CHANNEL", "ask-gsa"),
        mathcafe_channel=os.getenv("MATHCAFE_CHANNEL", "gsa-mathcafe"),
        mathcafe_enabled=os.getenv("MATHCAFE_ENABLED", "true").lower() == "true",
        admin_discord_id=int(raw_admin) if (raw_admin := os.getenv("ADMIN_DISCORD_ID", "").strip()) else None,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_enabled=os.getenv("TELEGRAM_ENABLED", "false").lower() == "true",
        football_api_key=os.getenv("FOOTBALL_API_KEY", ""),
        football_enabled=os.getenv("FOOTBALL_ENABLED", "false").lower() == "true",
        football_channel=os.getenv("FOOTBALL_CHANNEL", "world-cup-2026"),
        football_poll_interval=int(os.getenv("FOOTBALL_POLL_INTERVAL", "60")),
        telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_broadcast_target=os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHANNEL_ID", ""),
        groupme_enabled=os.getenv("GROUPME_ENABLED", "false").lower() == "true",
        groupme_bot_id=os.getenv("GROUPME_BOT_ID", ""),
        groupme_access_token=os.getenv("GROUPME_ACCESS_TOKEN", ""),
        groupme_group_id=os.getenv("GROUPME_GROUP_ID", ""),
        groupme_poll_interval=int(os.getenv("GROUPME_POLL_INTERVAL", "5")),
        dashboard_server_enabled=os.getenv("DASHBOARD_SERVER_ENABLED", "false").lower() == "true",
        dashboard_server_port=int(os.getenv("DASHBOARD_SERVER_PORT", "5555")),
    )


config = load_config()


# --- Live njit.edu search fallback (Sub-project 1) ---
# Fires only on a KB miss (no chunk, or top reranked relevance < LIVE_THRESHOLD).
# LIVE_ENABLED=0 disables the live path entirely (kill-switch). Key is in .env (never committed).
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
LIVE_ENABLED = os.getenv("LIVE_ENABLED", "1") == "1"
LIVE_THRESHOLD = float(os.getenv("LIVE_THRESHOLD", "0.15"))
# Office-tier (local prose fallback) relevance floor. The office prose corpus
# (type='office_page') is consulted only on a primary KB miss, and only adopted when its best
# chunk clears this floor — else fall through to the live njit.edu fallback. Default = LIVE_THRESHOLD.
OFFICE_THRESHOLD = float(os.getenv("OFFICE_THRESHOLD", str(LIVE_THRESHOLD)))
# Phase-2 deep-fallback chunk-rescue (M2). OFF by default. On a primary miss, consult the
# chunk index and ADOPT the rescued parent pages ONLY if they score strictly better than the
# existing chunks (no-regression contract). Distinct floor from LIVE_THRESHOLD (chunk-CE is
# not calibrated against whole-doc CE). Default 0.30 CALIBRATED in Task 7 on a full chunk-built
# copy: a threshold sweep + a 227-question full-pipeline A/B (deep OFF vs ON) showed 0.30 gives
# zero common-case false-adoption while preserving deep recall; the binding A/B fired on exactly
# 1/227 questions (a deflect -> correct improvement) with 0 regressions.
RETRIEVAL_DEEP_FALLBACK = os.getenv("RETRIEVAL_DEEP_FALLBACK", "").strip().lower() in ("1","true","yes","on")
DEEP_FALLBACK_THRESHOLD = float(os.getenv("DEEP_FALLBACK_THRESHOLD", "0.30"))
# A1 — gate the live tier. Two independent flags, both DEFAULT OFF = today's behavior (byte-identical).
# LIVE_RELEVANCE_GATE (answer-quality bundle): relevance-gate every live extract via the WS4 Gate-2
#   answerability judge (off-target page dropped), fetch up to 3 pages (was 2), and on no
#   grounded+relevant page degrade to an honest TOP-3-LINKS list instead of a bare "not found".
# LIVE_OPTIN (consent bundle): stop AUTO-firing live on a KB miss; deflect + OFFER instead (Telegram
#   button / an "ask: search njit for X" hint elsewhere). Explicit "search njit for X" + a tapped
#   offer stay direct (already consented). Recommended flip order: RELEVANCE_GATE first, then OPTIN.
LIVE_RELEVANCE_GATE = os.getenv("LIVE_RELEVANCE_GATE", "").strip().lower() in ("1","true","yes","on")
LIVE_OPTIN = os.getenv("LIVE_OPTIN", "").strip().lower() in ("1","true","yes","on")

# --- Answer-gate (WS4 — post-generation faithfulness / answerability gate) ---
# ANSWER_GATE_ENABLED defaults OFF so a commit is inert until an env flip + restart. The old
# ce_score BAND lever was retired in WS4 (the cross-encoder saturates ~0.96-1.0 on topical text,
# so the band was a dead lever); the gate now keys on deterministic answer-type grounding + a
# selective Gate-2 answerability call. (ANSWER_GATE_BAND removed — senior review #11.)
ANSWER_GATE_ENABLED = os.getenv("ANSWER_GATE_ENABLED", "0").strip().lower() in ("1","true","yes","on")

# --- A15b — topic→people trustworthiness (two behavior-changing flags, both DEFAULT OFF = today) ---
# MISS_SIGNAL_SKIP_UNSCORED (A11): the miss-signal (top_relevance) must skip an UNSCORED injected
#   profile card at rank-0 and read the first chunk that actually carries a cross-encoder score —
#   else a person-topic query falsely trips primary_miss → spurious deep-fallback/live. Flip after
#   an eval.sh kb/live/deflect diff is clean.
# PERSON_SCOPE_GUARD_ENABLED: on a person-seeking query, trim the compose context to chunks that
#   carry an NJIT-Person entity_id (drop seminar/external-visitor pages) so a non-NJIT person can
#   never be asserted as faculty. Fail-open, activates only when ≥1 entity chunk is in the pool.
# Terminal state = BOTH on (split only for independent eval-diffing); rollout is order-robust.
MISS_SIGNAL_SKIP_UNSCORED = os.getenv("MISS_SIGNAL_SKIP_UNSCORED", "").strip().lower() in ("1","true","yes","on")
PERSON_SCOPE_GUARD_ENABLED = os.getenv("PERSON_SCOPE_GUARD_ENABLED", "").strip().lower() in ("1","true","yes","on")

# FOLLOWUP_RESUME_ENABLED (default OFF): register a pending-action on offers/clarifies and resume it
# next turn. Off ⇒ no pending is set and the pre-check is skipped (pure current behavior). Flip in
# .env + restart to enable; backout = 0 (or revert).
FOLLOWUP_RESUME_ENABLED = os.getenv("FOLLOWUP_RESUME_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

# ANTECEDENT_GUARD_ENABLED (A3, default OFF): guard against a confident WRONG-person rewrite when a
# roster sits in history and a bare singular pronoun ("his/her") follow-up lets the LLM pick an
# arbitrary name. Two layers: (1) tag-at-source + a pre-LLM gate that CLARIFIES ("which one?") when
# the immediately-preceding assistant turn named ≥2 people; (2) a verify_rewrite backstop that
# passes-through when the picked name is a member of a ≥3-name list-chain with no standalone
# occurrence. Off ⇒ gate/backstop no-op and resolve_query returns the old resolution (zero behavior
# change). NOTE: person_names tags are ALWAYS written on turns regardless of this flag — they are
# inert when off (no consumer reads them). Flip in .env + restart to enable.
# Spec: docs/superpowers/specs/2026-07-04-a3-antecedent-ambiguity-design.md
ANTECEDENT_GUARD_ENABLED = os.getenv("ANTECEDENT_GUARD_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

# UNRESOLVED_PRONOUN_CLARIFY_ENABLED (Gap #2, default OFF): the twin of A3. When a BARE singular-personal
# pronoun follow-up ("is he working on ML?") cannot be resolved (LLM+verify pass it through unchanged),
# CLARIFY ("who do you mean?") instead of silently dropping the pronoun and answering a generic question.
# Post-LLM (so untagged-prose antecedents the LLM CAN resolve never clarify); bare-only (an in-message
# antecedent like "who is X and what's his Y" never nags). Off ⇒ resolve_query returns the old passthrough
# (zero behavior change). Flip in .env + restart to enable.
# Spec: docs/superpowers/specs/2026-07-04-gap2-unresolvable-pronoun-clarify-design.md
UNRESOLVED_PRONOUN_CLARIFY_ENABLED = os.getenv(
    "UNRESOLVED_PRONOUN_CLARIFY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


# --- Kavosh v2.1 unified router (Phase 1b) ---
# ROUTER_V21 master switch (default OFF). When on, the UnifiedRouter is built + consulted.
# ROUTER_V21_SHADOW (default ON): compute+log the new decision but ACT on the current path
# (flip to ACT by setting it 0 only after the flip-gate sign-off — the flag stays a kill-switch).
# ROUTER_V21_SLOT_RECOVERY (default OFF): Phase-2 LLM slot-recovery sub-flag, out of Phase 1b.
ROUTER_V21 = os.getenv("ROUTER_V21", "0").strip().lower() in ("1", "true", "yes", "on")
ROUTER_V21_SHADOW = os.getenv("ROUTER_V21_SHADOW", "1").strip().lower() in ("1", "true", "yes", "on")
ROUTER_V21_SLOT_RECOVERY = os.getenv("ROUTER_V21_SLOT_RECOVERY", "0").strip().lower() in ("1", "true", "yes", "on")
