"""Platform-agnostic message handler — the shared brain for all connectors."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from bot.services.food_detector import format_food_text, get_food_events
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FAREWELL,
    INTENT_FOOD,
    INTENT_FREE_MODE,
    INTENT_GREETING,
    INTENT_GSA_MODE,
    INTENT_HELP,
    INTENT_IDENTITY,
    INTENT_QUESTION,
    INTENT_SOCIAL,
    INTENT_THANKS,
)
from bot.services.retriever import SOURCE_FRIENDLY_NAMES
from bot.core.context_rewrite import resolve_query
from bot.core.deflection import looks_like_deflection
from bot.core.live_query import parse_explicit_live_search, LIVE_NOT_FOUND_MSG
from bot.core.live_fallback import maybe_answer_live
from v2.integration.njit_search import search as brave_search
from v2.core.ingestion.explore import http_fetch
from v2.core.retrieval.route_shadow import log_shadow
from v2.core.retrieval.answer_gate import gate1_intent, gate2_prompt, parse_gate2
from v2.core.retrieval import faithfulness as faith
import bot.config as botcfg

logger = logging.getLogger(__name__)

# Canned KB-miss deflection — used in three places: the normal no-chunks branch, the
# gate-2 answerability-deflect branch, and the Gate-1 pre-retrieval deflect. Single
# constant so the wording can never drift between them.
_KB_MISS_RESPONSE = (
    "I wasn't able to find specific information about that "
    "in the GSA knowledge base.\n\n"
    "For accurate information, please:\n"
    "- Visit the GSA office at Campus Center 110A (weekdays 11AM–5PM)\n"
    "- Email us at gsa-pres@njit.edu\n"
    "- Use /contact to find the right officer"
)

_OFFICER_FIRST_NAMES = {
    "fernando", "mohammad", "mohith", "durvish", "nistha", "ritwik",
}

# Deterministic clarify template (v2.1 UnifiedRouter CLARIFY family). Abstention is BUILT-but-OFF
# in Phase 1b, so this is reached only if a classifier ever returns CLARIFY directly.
_CLARIFY_MSG = (
    "I want to make sure I answer the right thing — could you rephrase or add a bit more detail? "
    "For example, name the department, person, or topic you mean."
)

# The intents the legacy handle() treats as whole-message commands (mirrors the v2.1 command layer).
# Used only to label the LEGACY decision for shadow agreement (review F1).
_LEGACY_COMMAND_INTENTS = {
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
}

FREE_MODE_SYSTEM_PROMPT = (
    "You are GSA Gateway (current version: Kavosh v2.1), NJIT's Graduate Student "
    "Association assistant. The student has switched to general chat mode. Answer helpfully "
    "and conversationally. You may answer questions beyond GSA topics, but "
    "periodically remind students you can also help with GSA events, funding, "
    "and campus resources."
)


# Sentence(s) where the model editorialises about which docs it used ("Note that I did not
# use doc_id 17745 …"). Stripped from BOTH the displayed answer AND the text handed to
# _source_note_for — otherwise the footer would credit a doc the model explicitly disclaimed.
# SAFETY: the sentence must actually mention a doc_id (lookahead) — otherwise a legitimate
# answer like "Students who did not use their meal plan…" would be wrongly deleted (never-withhold).
_META_DOC_SENTENCE_RX = re.compile(
    r"(?i)(?:^|\n|(?<=[.!?]\s))"                       # sentence start
    r"(?=[^.!?\n]*\bdoc_?id\b)"                        # ONLY sentences that mention a doc_id
    r"[^.!?\n]*\b(?:did not use|note that i|i (?:did not use|chose|used|relied))\b"
    r"[^.!?\n]*[.!?]\s*")


def _strip_meta_doc_sentences(text: str) -> str:
    """Remove model meta-commentary sentences about document usage. Run BEFORE doc_id
    harvesting so a 'did not use doc_id N' aside can never credit an unused source."""
    return _META_DOC_SENTENCE_RX.sub(" ", text or "")


def _strip_doc_citations(text: str) -> str:
    """Strip internal doc_id citation artifacts and model meta-commentary from the
    user-facing answer.  Called AFTER _source_note_for has already harvested doc_ids."""
    t = _strip_meta_doc_sentences(text)          # drop "did not use doc_id …" asides first
    # "According to doc_id N (source): ..." -> keep the sentence content after the connector
    t = re.sub(r"(?i)according to doc_id\s*\d+\s*(?:\([^)]*\))?\s*[:,-]?\s*", "", t)
    # bare or parenthesised "doc_id N" tokens
    t = re.sub(r"(?i)\(?\bdoc_id\s*\d+\b\)?", "", t)
    # tidy doubled spaces and misplaced punctuation
    t = re.sub(r"\s{2,}", " ", t).replace(" .", ".").replace(" ,", ",").strip()
    return t


def _source_note_for(answer_text: str, chunks) -> str:
    """Credit the source(s) the answer ACTUALLY cited (its 'doc_id N' references), in
    citation order; fall back to the top-ranked retrieved chunks, in rank order. Avoids the
    old bug of crediting an unordered ``set`` of every retrieved chunk — which surfaced
    unrelated near-duplicate docs (e.g. sibling club 'About' pages) and dropped the real one."""
    cited_ids = [int(m) for m in re.findall(r"doc_id\s*(\d+)", answer_text or "")]
    by_id = {getattr(c, "item_id", None): c for c in chunks}
    ordered: list[str] = []
    seen: set[str] = set()
    for did in cited_ids:                       # 1) what the answer cited, in order
        c = by_id.get(did)
        if c and c.source_file and c.source_file not in seen:
            seen.add(c.source_file); ordered.append(c.source_file)
    if not ordered:                             # 2) fallback: top-ranked chunks, rank order
        for c in chunks:
            if c.source_file and c.source_file not in seen:
                seen.add(c.source_file); ordered.append(c.source_file)
    names = [SOURCE_FRIENDLY_NAMES.get(f, f) for f in ordered[:2]]
    return " & ".join(names)


def _deep_adopt(current_rel, rescue_rel, threshold) -> bool:
    """Adopt deep-rescue chunks iff they clear the floor AND beat what's already there.
    current_rel None => no usable primary chunk, so any rescue >= threshold is an improvement."""
    if rescue_rel is None or rescue_rel < threshold:
        return False
    return current_rel is None or rescue_rel > current_rel


# Substrings that mark a message as structured-/person-looking enough to attempt the router.
# Includes the Scholar surfacing PULL cues (paper / citation-trend) so those queries are not
# dropped before routing — without them, "X most cited paper" (>4 words, no other cue) would
# never reach the deterministic papers/trend skills and would be reworded by the LLM.
_STRUCTURED_CUES = (
    "who ", "who'", "which ", "list ", " all ", "how many", "department",
    "faculty", "professor", "prof", "works on", "work on", "working on",
    "research", "area", "studies", "studying", "specializ", "expert",
    "tell me about", "about ", "e-mail", "email", "office", "phone",
    "title", "dean", "chair", "director", "head of", "name", "show",
    "every", "any ", "contact", "reach",
    # Scholar surfacing pull path (papers + citation trend):
    "paper", "publication", "article", "cited", "citation", "newest",
    "latest", "peak", "trend", "grow", "h-index", "h index", "i10")


def _structured_pregate(text: str) -> bool:
    """True when a message looks structured/person-directed enough to open a DB connection and
    attempt ``router.route``. Short messages (<=4 words, likely a bare name like "Guiling Wang")
    always pass so the entity layer can resolve them; longer ones need a cue substring."""
    low = text.lower()
    return any(c in low for c in _STRUCTURED_CUES) or len(low.split()) <= 4


@dataclass
class MessageRequest:
    user_id: str
    text: str
    platform: str               # "discord" | "telegram"
    guild_id: Optional[int] = None


@dataclass
class MessageResponse:
    text: str
    source_note: Optional[str] = None
    used_ai: bool = False
    ollama_failed: bool = False
    question_id: Optional[int] = None
    offer_live_search: bool = False   # connector should attach a "search NJIT's website" offer
    is_live: bool = False             # answer came from the live njit.edu fallback (verbatim extract)
    is_deep: bool = False             # answer came from the deep-fallback chunk-rescue


class MessageHandler:
    def __init__(
        self,
        retriever,
        ollama,
        conversation_manager,
        intent_detector,
        db,
        rate_limiter,
        kb,
        config,
        unified_router=None,
    ) -> None:
        self.retriever = retriever
        self.ollama = ollama
        self.conversation_manager = conversation_manager
        self.intent_detector = intent_detector
        self.db = db
        self.rate_limiter = rate_limiter
        self.kb = kb
        self.config = config
        self.unified_router = unified_router    # Kavosh v2.1 UnifiedRouter (None unless ROUTER_V21)

    async def handle(self, req: MessageRequest) -> MessageResponse:
        user_id = req.user_id

        # Rate limiting
        if self.rate_limiter and not self.rate_limiter.is_allowed(user_id):
            remaining = getattr(self.rate_limiter, "get_retry_after", lambda _: 30)(user_id)
            return MessageResponse(
                text=f"You're sending messages too quickly. Please wait {int(remaining)} seconds."
            )

        clean_text = req.text.strip()
        if not clean_text:
            return MessageResponse(text="")

        # ── Contextual follow-up resolution (accuracy backlog #2) ─────────────
        # Resolve a follow-up ("what is his position") into a standalone query using conversation
        # history, BEFORE routing/retrieval. `clean_text` stays the ORIGINAL (display, logging,
        # history, compose); `resolved_query` drives the router + retriever ONLY. Gated to follow-up
        # signals + history (one LLM call), skipped in free mode, passthrough on any doubt; a
        # hallucinated antecedent is discarded by the entity-membership guard. Spec 2026-06-22.
        mode = self.conversation_manager.get_mode(user_id) if self.conversation_manager else "gsa"
        resolved_query = clean_text

        # ── Follow-up resume (thread A) ───────────────────────────────────────
        # A pending offer/clarify from last turn: match this reply to an option and EXECUTE it,
        # instead of routing the raw token. Runs BEFORE context-rewrite so "yes" is never rewritten.
        # One-shot: cleared regardless. Flag-gated.
        if botcfg.FOLLOWUP_RESUME_ENABLED and self.conversation_manager is not None:
            try:
                _pending = self.conversation_manager.get_pending(user_id)
                if _pending is not None:
                    from bot.core.followup_match import match_followup, DECLINE
                    self.conversation_manager.clear_pending(user_id)   # one-shot, before execute (a resume may re-offer)
                    _idx = match_followup(clean_text, _pending.options)
                    if _idx is DECLINE:
                        ack = "No problem — what else can I help you with?"
                        self.conversation_manager.add_turn(user_id, "user", clean_text)
                        self.conversation_manager.add_turn(user_id, "assistant", ack)
                        return MessageResponse(text=ack)
                    if _idx is not None:
                        _resumed = await self._resume_pending(_pending.options[_idx])
                        if _resumed is not None:
                            self.conversation_manager.add_turn(user_id, "user", clean_text)
                            self.conversation_manager.add_turn(user_id, "assistant", _resumed[:500])
                            return MessageResponse(text=_resumed)
                        # recognized but execution FAILED → graceful stop; NEVER fall through to route the token
                        sorry = "Sorry — I couldn't pull that up just now. Could you ask again?"
                        self.conversation_manager.add_turn(user_id, "user", clean_text)
                        self.conversation_manager.add_turn(user_id, "assistant", sorry)
                        return MessageResponse(text=sorry)
                    # _idx is None → unrecognized reply → pending already cleared → fall through, route normally
            except Exception:  # noqa: BLE001 - never break the answer path; fall through to routing
                logger.debug("followup resume pre-check failed (ignored)", exc_info=True)

        if mode != "free" and self.ollama and self.conversation_manager:
            _max_turns = getattr(self.config, "conversation_max_turns", 5)
            _hist = self.conversation_manager.get_history(user_id, max_turns=_max_turns)
            resolved_query, _ = await resolve_query(clean_text, _hist, self.ollama)

        # ── Explicit "search njit for X" ──────────────────────────────────────
        # The user literally asked to go to the live njit.edu site, so honor it directly —
        # wins BEFORE the structured router AND the v2.1 router (they'd answer a different
        # question). This deterministic trigger must precede the ACT branch — review F2.
        explicit_topic = parse_explicit_live_search(clean_text)
        if explicit_topic is not None:
            return await self._answer_explicit_live(req, explicit_topic)

        # ── Answer-gate Gate-1 (deterministic intent deflect, pre-retrieval) ───
        # GSA mode only (not free, not judging); gate DEFAULTS OFF (ANSWER_GATE_ENABLED=0).
        # EXEMPT structured/KG (review BLOCKER): only deflect when Gate-1 fires AND the
        # deterministic layer cannot answer it — so a personal/live-PHRASED but structured-
        # answerable query (e.g. a KG role/contact lookup) is never withheld. _try_structured
        # runs only on the rare Gate-1-fire path (short-circuit); structured-answerable falls
        # through to the router, which produces the proper response shape.
        if botcfg.ANSWER_GATE_ENABLED and mode == "gsa":
            _g1 = gate1_intent(clean_text)
            if _g1.deflect and await self._try_structured(resolved_query) is None:
                logger.debug("answer-gate G1 deflect cue=%s q=%r", _g1.cue, clean_text[:80])
                return MessageResponse(text=_KB_MISS_RESPONSE)

        # ── Kavosh v2.1 UnifiedRouter ─────────────────────────────────────────
        # ROUTER_V21 + SHADOW: compute the new decision and only LOG it (answer still comes
        #   from the existing flow until the flip gate).
        # ROUTER_V21 + not SHADOW (flipped): ACT on the decision. COMMAND falls through to the
        #   legacy intent flow (same IntentDetector → identical handling, no duplication); all
        #   other families are answered by _answer_decision. A decide() exception degrades to the
        #   legacy path — the router never breaks the answer path.
        if botcfg.ROUTER_V21 and self.unified_router is not None:
            decision = None
            try:
                decision = self.unified_router.decide(resolved_query)
            except Exception:  # noqa: BLE001 - router must never break the answer path
                logger.debug("router-v21 decide failed (ignored)", exc_info=True)
            if decision is not None:
                if botcfg.ROUTER_V21_SHADOW:
                    try:
                        cur_family = await self._legacy_family(resolved_query, user_id)
                    except Exception:  # noqa: BLE001 - shadow must never break the answer path
                        cur_family = None
                    log_shadow({"message": clean_text[:200],
                                "new_family": decision.family, "new_skill": decision.skill,
                                "current_family": cur_family,
                                "agree": (cur_family == decision.family) if cur_family else None})
                elif decision.family != "COMMAND":
                    return await self._answer_decision(req, decision, resolved_query)
                # ACT + COMMAND → fall through to the legacy command/intent handling below

        # ── Structured retrieval (enumerate/filter/traverse/count) ────────────
        # Tried BEFORE intent detection on purpose: phrasings like "list all CS
        # faculty" or "who works on social network analysis" otherwise mis-classify
        # as statement/food/social. Returns None for anything not clearly structured,
        # so descriptive questions fall straight through to the unchanged RAG path.
        # GSA-MODE ONLY: in free (general chat) mode the user wants the general LLM,
        # NOT a GSA structured answer — skip structured so free mode isn't identical to GSA.
        # (mode already resolved above for the contextual-rewrite gate)
        if mode != "free":
            structured = await self._try_structured(resolved_query, user_id=user_id, clean_text=clean_text)
            if structured is not None:
                return MessageResponse(text=structured)

        # Detect intent
        if self.intent_detector:
            intent, _ = self.intent_detector.detect(clean_text)
        else:
            intent = INTENT_QUESTION

        # ── Non-RAG intents ──────────────────────────────────────────────────

        if intent == INTENT_CLEAR_HISTORY:
            if self.conversation_manager:
                self.conversation_manager.clear_session(user_id)
            return MessageResponse(
                text="Conversation cleared! Starting fresh. What would you like to know about GSA?"
            )

        if intent == INTENT_GREETING:
            session = (
                self.conversation_manager.get_session(user_id)
                if self.conversation_manager
                else None
            )
            if session and len(session.turns) > 0:
                text = (
                    "Welcome back! Kavosh here — what else would you like to explore?\n"
                    "_(Type 'clear' to start a new conversation)_"
                )
            else:
                text = (
                    "سلام · Hola · नमस्ते · 你好 · হ্যালো · ආයුබෝවන් · Olá · Merhaba · Hello\n"
                    "_Don't see your language? Ask Mohammad — he'll happily add it!_\n\n"
                    "Hi! I'm **GSA Gateway** — NJIT's Graduate Student Association assistant, and the "
                    "wider NJIT community's too. _(Current version: **Kavosh v2.1** — کاوش, \"exploration.\")_\n\n"
                    "What I can help you explore:\n"
                    "- 🔬 **NJIT faculty across every college** — who works on a topic, their research areas & citations\n"
                    "- 🏫 **Departments, programs & who's who** — deans, chairs, directors\n"
                    "- 🧭 **Campus resources & offices** across NJIT\n"
                    "- 🎓 **GSA** — events, the MMI Workshop series, travel awards & funding\n"
                    "- 👥 **GSA officers, club/RGO rules & the constitution**\n\n"
                    "Just ask me anything — I answer from real NJIT/GSA sources, in English."
                )
            return MessageResponse(text=text)

        if intent == INTENT_FAREWELL:
            vpa = self.kb.contacts.get("vp_academic_affairs") if self.kb else None
            vpa_name  = vpa.name  if vpa else "Mohammad Dindoost"
            vpa_email = vpa.email if vpa else "gsa-vpa@njit.edu"
            return MessageResponse(
                text=(
                    "خداحافظ · Adiós · अलविदा · 再见 · বিদায় · Tchau · Hoşçakal · Goodbye\n\n"
                    "It was great exploring with you! Come back anytime — Kavosh will be here.\n\n"
                    f"For any academic questions or GSA matters, reach out to your "
                    f"VP Academic Affairs:\n"
                    f"**{vpa_name}** — {vpa_email} · md724@njit.edu"
                )
            )

        if intent == INTENT_THANKS:
            return MessageResponse(
                text="You're welcome! Let me know if you have more questions about GSA."
            )

        if intent == INTENT_HELP:
            return MessageResponse(
                text=(
                    "Here's how to use GSA Gateway:\n\n"
                    "Just type your question naturally!\n\n"
                    "Commands:\n"
                    "- /events — see upcoming events\n"
                    "- /contact [role] — find GSA contacts\n"
                    "- /resources [category] — campus resources\n\n"
                    "Tips:\n"
                    "- Ask follow-up questions naturally\n"
                    "- Type 'clear' to reset our conversation"
                )
            )

        if intent == INTENT_IDENTITY:
            model_name = self.ollama.model if self.ollama else None
            if model_name:
                text = (
                    "I'm **GSA Gateway**, NJIT's Graduate Student Association assistant — and the wider "
                    "NJIT community's too. You're talking to my current version, **Kavosh v2.1** "
                    "(کاوش — *exploration, discovery*), successor to **Binesh** (*insight*), which retired "
                    "June 15, 2026.\n\n"
                    f"I run on **{model_name}** — a local language model on NJIT infrastructure, not a cloud "
                    "service. Unlike ChatGPT, I'm purpose-built for NJIT: my answers come straight from "
                    "official **GSA** documents *and* NJIT's **knowledge graph** of faculty, research, and "
                    "departments across **every college** — not the open web, and not general topics unrelated "
                    "to NJIT.\n\n"
                    "What I can help you explore:\n"
                    "- 🔬 NJIT faculty across every college — who works on a topic, their research areas & citations\n"
                    "- 🏫 Departments, programs & who's who (deans, chairs, directors)\n"
                    "- 🧭 Campus resources & offices\n"
                    "- 🎓 GSA events, the MMI Workshop series, travel awards & funding\n"
                    "- 👥 GSA officers, club/RGO rules & the constitution\n\n"
                    "md724@njit.edu\n\n"
                    "🛠️ Open source — explore the code or contribute on "
                    "[GitHub](https://github.com/mdindoost/gsa-gateway)."
                )
            else:
                text = (
                    "I'm **GSA Gateway** (current version: **Kavosh v2.1** — \"exploration\"), NJIT's Graduate "
                    "Student Association assistant and a guide to the wider NJIT community — faculty, "
                    "research, departments, and GSA services. md724@njit.edu. "
                    "🛠️ Open source — contribute on [GitHub](https://github.com/mdindoost/gsa-gateway)."
                )
            return MessageResponse(text=text)

        if intent == INTENT_FREE_MODE:
            if not self.ollama:
                return MessageResponse(
                    text=(
                        "General chat mode requires the AI engine, which isn't available right now. "
                        "I'll continue answering GSA questions from the knowledge base."
                    )
                )
            if self.conversation_manager:
                self.conversation_manager.set_mode(user_id, "free")
            return MessageResponse(
                text="Switched to **General Chat Mode**. Ask me anything! Type `gsa mode` to return to GSA topics."
            )

        if intent == INTENT_GSA_MODE:
            if self.conversation_manager:
                self.conversation_manager.set_mode(user_id, "gsa")
            return MessageResponse(
                text="Switched back to **GSA Mode**. I'll answer from official GSA documents."
            )

        # ── RAG pipeline ──────────────────────────────────────────────────────
        return await self._rag_pipeline(req, clean_text, intent, resolved_query=resolved_query)

    async def _try_structured(self, text: str, user_id: str | None = None,
                              clean_text: str | None = None) -> Optional[str]:
        """Answer enumerate/filter/traverse/count questions from structured DB queries
        (complete + deterministic), or return None to fall through to semantic RAG.

        The skill queries run on a fresh connection in a worker thread (no event-loop
        blocking). The deterministic facts text is the answer; the LLM only rephrases
        it, and we fall back to the facts verbatim if the LLM is down."""
        db_path = getattr(self.db, "db_path", None) if self.db else None
        if not db_path:
            return None
        # cheap pre-gate: open a connection only for structured-/person-looking questions.
        if not _structured_pregate(text):
            return None

        def _run() -> Optional[str]:
            import sqlite3
            from v2.core.retrieval import router as srouter, structured_answer
            conn = sqlite3.connect(db_path, timeout=5)  # FTS+plain SQL only, no vec
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                rt = srouter.route(conn, text)
                if rt is None:
                    return None
                result = structured_answer.run(conn, rt)
                facts = structured_answer.format_answer(result)
                if not facts:
                    return None
                # A deterministic line (profile links / Scholar metrics) appended to the
                # FINAL answer verbatim — never handed to the LLM to restate.
                # Metric answers are themselves deterministic (numbers must not be reworded) →
                # flag so the caller skips LLM compose entirely.
                return (rt, facts, structured_answer.deterministic_suffix(result),
                        structured_answer.is_deterministic(result))
            finally:
                conn.close()

        try:
            ran = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - never break the message path; fall to RAG
            logger.warning("Structured retrieval errored, falling back to RAG: %s", exc)
            return None
        if not ran:
            return None
        rt, facts, suffix, deterministic = ran
        composed = await self._compose_structured(text, facts, suffix, deterministic)
        if user_id is not None:                      # main :290 path → register + record
            self._register_and_record(user_id, clean_text or text, rt, composed)
        return composed

    async def _compose_structured(self, text: str, facts: str, suffix: str,
                                  deterministic: bool) -> str:
        """Compose-suppression — SHARED by _try_structured and the v2.1 _answer_decision so the
        anti-fab rule can't drift between the two paths. The LLM rephrases the Facts ONLY when the
        answer is NOT deterministic (metric numbers must never be reworded); the deterministic
        suffix (profile links / Scholar numbers) is appended VERBATIM, never handed to the LLM."""
        out = facts
        if self.ollama and not deterministic:   # metric numbers must not be reworded by the LLM
            composed = await self.ollama.compose_from_rows(text, facts)
            if composed:
                out = composed
        if suffix:
            out = f"{out}\n\n{suffix}"
        return out

    def _register_and_record(self, user_id, clean_text, rt, text) -> None:
        """Side-effect chokepoint for BOTH structured paths (_answer_decision + _try_structured):
        register a resumable pending action AND record the answer in history (Bug 1 / G6). The WHOLE
        body is flag-gated so flag-off is truly zero-behavior-change — with the flag off, structured
        answers are NOT added to history (unchanged current behavior). No return — the caller builds
        its own MessageResponse."""
        from datetime import datetime, timezone
        from v2.core.retrieval import structured_answer
        from bot.core.pending import PendingAction, PendingOption
        cm = self.conversation_manager
        if cm is None or not botcfg.FOLLOWUP_RESUME_ENABLED:   # flag off ⇒ fully inert (Fable #2)
            return
        try:
            resumable = structured_answer.resumable_action(rt)
            if resumable:
                cm.set_pending(user_id, PendingAction(
                    options=[PendingOption(label, "structured",
                                           {"skill": r.skill, "args": r.args}) for (label, r) in resumable],
                    created_at=datetime.now(timezone.utc)))
            cm.add_turn(user_id=user_id, role="user", content=clean_text)
            cm.add_turn(user_id=user_id, role="assistant", content=(text or "")[:500])
        except Exception:  # noqa: BLE001 - never break the answer path
            logger.debug("followup register_and_record failed (ignored)", exc_info=True)

    async def _resume_pending(self, option) -> "Optional[str]":
        """Execute a pending option's structured resume, bypassing the router (deterministic).
        Returns composed text, or None on any failure (caller → graceful stop)."""
        if option.action != "structured":
            return None
        skill = option.payload.get("skill"); args = option.payload.get("args") or {}
        try:
            ran = await asyncio.to_thread(self._structured_from_route, skill, args)
        except Exception as exc:  # noqa: BLE001 - never break the message path
            logger.warning("followup resume errored: %s", exc)
            return None
        if not ran:
            return None
        facts, suffix, deterministic = ran        # _structured_from_route returns a 3-tuple (unchanged)
        return await self._compose_structured(option.label, facts, suffix, deterministic)

    def _structured_from_route(self, skill: str, args: dict):
        """SQL body for a DECIDED skill/args (no route() — the UnifiedRouter already resolved it).
        Thread target. Returns (facts, suffix, deterministic) or None (empty → caller falls to RAG)."""
        import sqlite3
        from v2.core.retrieval import structured_answer
        from v2.core.retrieval.router import Route
        db_path = getattr(self.db, "db_path", None) if self.db else None
        if not db_path:
            return None
        conn = sqlite3.connect(db_path, timeout=5)  # FTS+plain SQL only, no vec
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            result = structured_answer.run(conn, Route(skill=skill, args=dict(args or {})))
            facts = structured_answer.format_answer(result)
            if not facts:
                return None
            return (facts, structured_answer.deterministic_suffix(result),
                    structured_answer.is_deterministic(result))
        finally:
            conn.close()

    async def _legacy_family(self, clean_text: str, user_id: str) -> str:
        """The family the LEGACY handler path would route this to — for shadow agreement (review F1).
        Mirrors handle()'s legacy ordering: free → RAG (skips structured); else a structured answer
        → KG; else a command intent → COMMAND; else RAG. Runs `_try_structured` (extra SQL), which is
        acceptable in shadow (a temporary measurement mode, not the hot path)."""
        mode = self.conversation_manager.get_mode(user_id) if self.conversation_manager else "gsa"
        if mode != "free":
            try:
                if await self._try_structured(clean_text) is not None:
                    return "KG"
            except Exception:  # noqa: BLE001 - shadow measurement only
                pass
        intent = self.intent_detector.detect(clean_text)[0] if self.intent_detector else INTENT_QUESTION
        if intent in _LEGACY_COMMAND_INTENTS:
            return "COMMAND"
        return "RAG"

    async def _answer_decision(self, req: MessageRequest, decision,
                               resolved_query: str | None = None) -> MessageResponse:
        """ACT on a UnifiedRouter RouteDecision (ROUTER_V21 + flipped). KG runs the deterministic
        structured answer (compose-suppression preserved via _compose_structured — numbers/links are
        never reworded/hallucinated); an EMPTY structured result degrades to RAG (honest-partial,
        never a fabricated answer). RAG/LIVE/CLARIFY/OTHER reuse the existing handlers (full
        MessageResponse fidelity — source notes, buttons, live flag). COMMAND is handled by the
        legacy flow and never reaches here."""
        text = req.text.strip()
        fam = decision.family
        if fam == "KG":
            # Free (general chat) mode skips the GSA structured path — the user wants the general
            # LLM, so a KG decision degrades to the RAG pipeline (which handles free mode). Preserves
            # the "free skips structured" invariant the legacy path enforces at handle(). [review F3]
            mode = self.conversation_manager.get_mode(req.user_id) if self.conversation_manager else "gsa"
            if mode == "free":
                return await self._rag_pipeline(req, text, INTENT_QUESTION, resolved_query=resolved_query)
            try:
                ran = await asyncio.to_thread(self._structured_from_route,
                                              decision.skill, decision.args)
            except Exception as exc:  # noqa: BLE001 - never break; fall to RAG
                logger.warning("router-v21 structured run errored, falling to RAG: %s", exc)
                ran = None
            if ran:
                facts, suffix, deterministic = ran
                text = await self._compose_structured(text, facts, suffix, deterministic)
                from v2.core.retrieval.router import Route
                self._register_and_record(req.user_id, req.text.strip(),
                                          Route(skill=decision.skill, args=dict(decision.args or {})), text)
                return MessageResponse(text=text)
            return await self._rag_pipeline(req, text, INTENT_QUESTION, resolved_query=resolved_query)
        if fam == "RAG":
            rag_intent = INTENT_FOOD if decision.source == "food" else INTENT_QUESTION
            return await self._rag_pipeline(req, text, rag_intent, resolved_query=resolved_query)
        if fam == "LIVE":
            return await self._answer_explicit_live(req, text)
        if fam == "CLARIFY":
            return MessageResponse(text=_CLARIFY_MSG)
        # OTHER / anything unexpected → RAG (never fabricate)
        return await self._rag_pipeline(req, text, INTENT_QUESTION)

    async def retry_question(self, req: MessageRequest) -> MessageResponse:
        """Re-run RAG at temperature=0.7 for the 🔄 retry button.

        Skips rate limiting and intent detection — the original request already
        passed both.  Returns a fresh MessageResponse with a new question_id.
        """
        return await self._rag_pipeline(
            req, req.text.strip(), INTENT_QUESTION, temperature=0.7
        )

    async def live_search(self, question: str):
        """The single seam to the live njit.edu extractive fallback. Constructs the provider
        wiring + feature-gate in ONE place, so the auto-fire path and the connector offer-tap
        path can't drift. Returns a LiveAnswer or None (None when the feature is off / no key /
        no Ollama — so a stale tapped button degrades gracefully instead of crashing)."""
        if not (botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY and self.ollama):
            return None
        return await maybe_answer_live(
            question,
            search_fn=brave_search,
            fetch_fn=http_fetch,
            generate=lambda system, user: self.ollama.generate(user, system),
        )

    async def _answer_explicit_live(self, req: MessageRequest, topic: str) -> MessageResponse:
        """Run a direct live njit.edu search for an explicit 'search njit for X' request.
        Logged with a question_id (normal 👍/👎/🔄 buttons), but NO web-re-search offer (it
        just searched). Empty result → the shared 'found nothing' message."""
        live = await self.live_search(topic)
        if live is None:
            return MessageResponse(text=LIVE_NOT_FOUND_MSG)
        text = live.text
        question_id: Optional[int] = None
        if self.db:
            question_id = self.db.log_question(
                user_id=req.user_id, question=req.text, matched_topic="live njit.edu (explicit)",
                confidence=100.0, guild_id=req.guild_id, platform=req.platform,
            )
        return MessageResponse(
            text=text, source_note=live.source_url, used_ai=True, question_id=question_id,
            is_live=True,
        )

    async def _faithfulness_gate(self, question: str, answer: str, chunks) -> tuple[bool, str]:
        """Post-generation answerability gate (WS4). Returns (keep, reason). keep=False => the
        composed answer is not a trustworthy response to THIS question, so the caller abstains.

        Deterministic first (subjective guard + answer-type grounding); only the non-typed factual
        residual costs a Gate-2 LLM call. A composed answer that EXPLICITLY declines (Granite
        self-declined) is treated as an honest abstain — but NOT one that merely cites a source
        (the broad deflection detector over-triggered on njit.edu pointers under temp>0 variance)."""
        if not answer or faith.is_explicit_nonanswer(answer):
            return False, "self-abstain"
        passages = [(getattr(c, "text", "") or "")[:1200] for c in chunks[:5]]
        outcome, reason = faith.assess_pre_gate2(question, answer, passages)
        if outcome == "gate2" and self.ollama:
            sys_p, usr_p = gate2_prompt(question, passages)
            raw = await self.ollama.generate(
                prompt=usr_p, system=sys_p,
                options={"temperature": 0.0, "num_predict": 256}, fmt="json",
            ) or ""
            v = parse_gate2(raw)
            outcome, reason = faith.decide_after_gate2(v.label, v.quote, passages, parsed=v.parsed)
        elif outcome == "gate2":
            outcome = "answer"  # no LLM available -> never withhold (answer-biased, like parse_gate2)
        return outcome == "answer", reason

    def _useful_abstain(self, question: str, chunks) -> str:
        """The honest-abstain reply (WS4 Phase 4): an abstain should still help. Leads with the
        NEAREST thing retrieval surfaced (framed as related, not the exact answer) when there is a
        titled chunk, then routes the user to the existing deflection (GSA office / email / contact)."""
        top = chunks[0] if chunks else None
        title = (getattr(top, "section_title", None) or getattr(top, "title", None)) if top else None
        src = None
        if top is not None:
            src = getattr(top, "source_url", None) or SOURCE_FRIENDLY_NAMES.get(
                getattr(top, "source_file", ""), None)
        lead = "I wasn't able to find a specific answer to that in the GSA knowledge base."
        if title:
            lead += f" The closest related section I have is **{title}**"
            lead += f" ({src})." if src else "."
        return (
            f"{lead}\n\n"
            "For accurate information, please:\n"
            "- Visit the GSA office at Campus Center 110A (weekdays 11AM–5PM)\n"
            "- Email us at gsa-pres@njit.edu\n"
            "- Use /contact to find the right officer"
        )

    async def _rag_pipeline(
        self,
        req: MessageRequest,
        clean_text: str,
        intent: str,
        temperature: float = 0.3,
        resolved_query: str | None = None,
    ) -> MessageResponse:
        # `clean_text` stays the ORIGINAL (display/log/history/compose). `resolved_query` (a
        # context-resolved follow-up) drives RETRIEVAL only; None/equal → today's behavior. [backlog #2]
        user_id = req.user_id
        try:
            # Free mode: skip RAG entirely, go direct to LLM
            mode = self.conversation_manager.get_mode(user_id) if self.conversation_manager else "gsa"
            if mode == "free" and self.ollama:
                result = await self.ollama.generate(prompt=clean_text, system=FREE_MODE_SYSTEM_PROMPT)
                if self.conversation_manager:
                    self.conversation_manager.add_turn(user_id=user_id, role="user", content=clean_text)
                    if result:
                        self.conversation_manager.add_turn(
                            user_id=user_id, role="assistant", content=result[:500]
                        )
                if self.db:
                    self.db.log_question(
                        user_id=user_id,
                        question=clean_text,
                        matched_topic=None,
                        confidence=None,
                        guild_id=req.guild_id,
                        platform=req.platform,
                        mode="free",
                    )
                return MessageResponse(
                    text=result or "The AI engine didn't respond. Please try again.",
                    source_note="General Chat Mode",
                )

            chunks = []
            response_text = ""
            source_note = None
            used_ai = False
            ollama_failed = False

            # Conversation history
            history: list[dict] = []
            if self.conversation_manager:
                max_turns = getattr(self.config, "conversation_max_turns", 5)
                history = self.conversation_manager.get_history(user_id, max_turns=max_turns)

            # Expand short/officer queries. Retrieval is built from the context-resolved query
            # (`base_q`); clean_text stays original for compose/log/history.
            base_q = resolved_query or clean_text
            core = base_q.strip("?!.,").strip().lower()
            matched_officer = next(
                (name for name in _OFFICER_FIRST_NAMES if name in core.split() or core == name),
                None,
            )
            is_officer_query = matched_officer is not None
            search_query = base_q
            contact_filter = None

            if is_officer_query:
                search_query = (
                    f"Who is {matched_officer.title()} at GSA NJIT? "
                    f"Contact information and role for {matched_officer.title()}"
                )
                contact_filter = "contact"
            # NOTE: the v1 LLM short-query expander was REMOVED here (thread B, 2026-07-03). It
            # rewrote every short (<=3-word) query into a GSA-framed question, which mis-framed
            # non-GSA queries on the now university-wide corpus (e.g. "game development lab",
            # "computer science phd" -> GSA). Short queries now retrieve on base_q verbatim; this
            # reverses the 2026-06-22 [SE4] "WRAP don't replace" decision (the wrapper was safe on a
            # GSA-centric corpus, but became the bias it was meant to avoid once the corpus went
            # NJIT-wide). See docs/superpowers/specs/2026-07-03-remove-v1-expander-design.md.

            # Retrieve
            if intent == INTENT_FOOD:
                if self.retriever:
                    chunks = await self.retriever.retrieve_for_food_query()
                food_events = get_food_events(kb=self.kb, db=self.db, days_ahead=7)
                if food_events:
                    if self.conversation_manager:
                        self.conversation_manager.add_turn(
                            user_id=user_id, role="user", content=clean_text
                        )
                        self.conversation_manager.add_turn(
                            user_id=user_id,
                            role="assistant",
                            content="[Food events listed]",
                            source_files=["events.yml"],
                        )
                    food_q_id: Optional[int] = None
                    if self.db:
                        food_q_id = self.db.log_question(
                            user_id=user_id,
                            question=clean_text,
                            matched_topic="food events",
                            confidence=100.0,
                            guild_id=req.guild_id,
                            platform=req.platform,
                        )
                    return MessageResponse(
                        text=format_food_text(food_events),
                        source_note="GSA Events",
                        question_id=food_q_id,
                    )
            elif intent == INTENT_SOCIAL:
                if self.retriever:
                    chunks = await self.retriever.retrieve(
                        query="social events activities networking happy hour graduate students",
                        source_type_filter="event",
                    )
            elif self.retriever:
                chunks = await self.retriever.retrieve(
                    query=search_query,
                    conversation_history=history,
                    source_type_filter=contact_filter,
                )

            # Primary miss → LOCAL office tier BEFORE the live njit.edu fallback (precedence
            # ladder). "primary miss" reuses the existing signal: no usable chunk OR best
            # reranked relevance < LIVE_THRESHOLD. The office prose corpus (type='office_page',
            # excluded from the primary retrieve) is searched in ISOLATION and adopted only when
            # its OWN floor (OFFICE_THRESHOLD) is cleared — else we fall through to live.
            used_live = False
            used_office = False
            attempted_live = False   # auto-fire ran this turn (regardless of result)
            is_canned_deflection = False   # tag-at-source: our own "no info" reply
            # base_q = the resolved/expanded query (main's contextual-rewrite); used for the
            # primary-miss signal, the office tier, and live — so a rewritten follow-up drives all
            # three. clean_text stays the original for compose/log/history.
            relevance = self.retriever.top_relevance(base_q, chunks) if (self.retriever and chunks) else None
            primary_miss = (not chunks) or (relevance is not None and relevance < botcfg.LIVE_THRESHOLD)
            if primary_miss and self.retriever:
                office_chunks = await self.retriever.retrieve(
                    query=search_query, conversation_history=history, item_types=["office_page"])
                office_rel = self.retriever.top_relevance(base_q, office_chunks) if office_chunks else None
                if office_chunks and office_rel is not None and office_rel >= botcfg.OFFICE_THRESHOLD:
                    chunks = office_chunks            # generate from local office prose (KB)
                    used_office = True
            used_deep = False
            # Gate the deep rescue on corpus readiness: skip it when the chunk corpus
            # isn't built/coherent for the active model (corpus_ready() is cached + only
            # evaluated once the flag is on, so it stays inert until RETRIEVAL_DEEP_FALLBACK
            # flips). Flipping the flag on an un-built DB is then a safe no-op.
            if (primary_miss and not used_office and botcfg.RETRIEVAL_DEEP_FALLBACK
                    and self.retriever and self.retriever.corpus_ready()):
                _t0 = time.perf_counter()
                rescue = await self.retriever.retrieve_deep(base_q)   # query_vec reuse: see note
                elapsed_ms = (time.perf_counter() - _t0) * 1000
                rescue_rel = self.retriever.top_relevance(base_q, rescue) if rescue else None
                if rescue and _deep_adopt(relevance, rescue_rel, botcfg.DEEP_FALLBACK_THRESHOLD):
                    chunks = rescue
                    used_deep = True
                    primary_miss = False        # rescued -> do not fall through to live
                logger.info(
                    "deep-fallback: candidates=%d rescue_rel=%s adopted=%s %.0fms",
                    len(rescue or []),
                    f"{rescue_rel:.3f}" if rescue_rel is not None else "none",
                    used_deep,
                    elapsed_ms,
                )
            if (primary_miss and not used_office and botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                    and self.ollama and self.retriever):
                attempted_live = True
                live = await self.live_search(base_q)   # single seam (provider wiring + gate)
                if live is not None:
                    response_text = live.text
                    source_note = live.source_url
                    used_ai = True
                    used_live = True
                    logger.info("live njit.edu fallback answered from %s", live.source_url)

            # Generate — compose FIRST, then run the post-generation faithfulness / answerability
            # gate (WS4) on the composed answer. The gate is deterministic answer-type grounding
            # (count/rate/money/date must carry a GROUNDED value of the expected type) + a
            # subjective-superlative guard + robust markdown-normalized quote grounding, with a
            # Gate-2 answerability verdict only for the non-typed factual residual. It replaces the
            # brittle pre-generation quote_grounded/fact_shaped gate (which false-abstained on
            # markdown — the Chrome-River bug — while leaking grounded-but-irrelevant fabrications).
            if used_live:
                pass
            elif chunks and self.ollama:
                # Compose sees the ORIGINAL wording for fidelity, plus the resolved query (when a
                # follow-up was rewritten) so the question it answers matches the retrieved chunks
                # — avoids the split-brain where compose resolves a pronoun differently. [RA3]
                compose_question = clean_text
                if resolved_query and resolved_query != clean_text:
                    compose_question = f"{clean_text}\n(resolved for retrieval: {resolved_query})"
                ai_resp = await self.ollama.generate_answer(
                    question=compose_question,
                    chunks=chunks,
                    conversation_history=history,
                    temperature=temperature,
                )
                if ai_resp:
                    # Harvest the footer from a META-STRIPPED copy so a "did not use doc_id N"
                    # aside can't credit an unused source; then clean the displayed answer.
                    source_note = _source_note_for(_strip_meta_doc_sentences(ai_resp), chunks)
                    response_text = _strip_doc_citations(ai_resp)
                    used_ai = True
                    # ── Post-generation faithfulness / answerability gate (WS4) ──
                    # Guarded: a gate fault (regex/LLM) must NEVER discard the already-composed answer
                    # and fall through to the generic error — default to KEEP on any exception (senior #7).
                    if botcfg.ANSWER_GATE_ENABLED and intent not in (INTENT_FOOD, INTENT_SOCIAL):
                        try:
                            _keep, _why = await self._faithfulness_gate(base_q, response_text, chunks)
                        except Exception:
                            logger.exception("faithfulness-gate error; keeping composed answer")
                            _keep, _why = True, "gate-error-keep"
                        if not _keep:
                            # never-withhold: try an untried answering tier (live njit.edu) BEFORE
                            # abstaining — a deflect here withholds nothing a tier could supply.
                            if (not attempted_live and botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                                    and self.ollama and self.retriever):
                                attempted_live = True
                                live = await self.live_search(base_q)
                                if live is not None:
                                    response_text = live.text
                                    source_note = live.source_url
                                    used_live = True
                                    logger.info("faithfulness-gate->live answered from %s", live.source_url)
                            if not used_live:
                                response_text = self._useful_abstain(base_q, chunks)
                                source_note = None
                                used_ai = False
                                is_canned_deflection = True
                                attempted_live = True  # suppress the live-search offer on a gate abstain
                                logger.debug("faithfulness-gate abstain why=%s q=%r", _why, base_q[:80])
                else:
                    best = chunks[0]
                    response_text = (
                        f"{best.text[:800]}\n\n"
                        "_⚠️ The AI engine is temporarily unavailable. "
                        "This is raw information from the GSA knowledge base. "
                        "Please try again in a few minutes, or contact a GSA officer "
                        "at gsa-pres@njit.edu if this persists._"
                    )
                    source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)
                    ollama_failed = True
            elif chunks:
                best = chunks[0]
                response_text = best.text[:800]
                source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)
            else:
                response_text = _KB_MISS_RESPONSE
                is_canned_deflection = True

            # Office answers surface the authoritative njit.edu page as the verify-link (RA6),
            # mirroring the live-fallback's source_url note. Skip on an abstain (the gate set
            # source_note=None and the useful-abstain text already carries its own link — senior #9).
            if used_office and chunks and not is_canned_deflection:
                source_note = getattr(chunks[0], "source_url", None) or source_note

            # Deflection offer (offer-only — NEVER auto-fire). Detect a confident deflection:
            # tag-at-source (the canned no-info branch above) OR a narrow phrase-match on the
            # composed-from-chunks answer. Suppressed when the feature is off, when we already
            # answered live, or when this turn already tried live and got nothing (don't offer
            # to redo a search that just failed).
            is_deflection = is_canned_deflection or (
                bool(chunks) and used_ai and not used_live
                and looks_like_deflection(response_text)
            )
            offer_live_search = bool(
                botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                and is_deflection and not used_live and not attempted_live
            )

            # Update conversation memory
            if self.conversation_manager:
                self.conversation_manager.add_turn(
                    user_id=user_id, role="user", content=clean_text
                )
                self.conversation_manager.add_turn(
                    user_id=user_id,
                    role="assistant",
                    content=response_text[:500],
                    source_files=[c.source_file for c in chunks],
                )

            # Log to DB
            question_id: Optional[int] = None
            if self.db:
                question_id = self.db.log_question(
                    user_id=user_id,
                    question=clean_text,
                    matched_topic=chunks[0].section_title if chunks else None,
                    confidence=chunks[0].relevance_score * 100 if chunks else 0.0,
                    guild_id=req.guild_id,
                    platform=req.platform,
                )

            return MessageResponse(
                text=response_text,
                source_note=source_note,
                used_ai=used_ai,
                ollama_failed=ollama_failed,
                question_id=question_id,
                offer_live_search=offer_live_search,
                is_live=used_live,
                is_deep=used_deep,
            )

        except Exception as exc:
            logger.error("MessageHandler._rag_pipeline error: %s", exc, exc_info=True)
            return MessageResponse(
                text=(
                    "I encountered an error processing your question. "
                    "Please try again or contact a GSA officer at gsa-pres@njit.edu"
                )
            )
