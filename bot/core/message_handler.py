"""Platform-agnostic message handler — the shared brain for all connectors."""

from __future__ import annotations

import asyncio
import logging
import re
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
from bot.core.headsup import apply_headsup
from bot.core.deflection import looks_like_deflection
from bot.core.live_query import parse_explicit_live_search, LIVE_NOT_FOUND_MSG
from bot.core.live_fallback import maybe_answer_live
from v2.integration.njit_search import search as brave_search
from v2.core.ingestion.explore import http_fetch
from v2.core.retrieval.route_shadow import log_shadow
import bot.config as botcfg

logger = logging.getLogger(__name__)

_OFFICER_FIRST_NAMES = {
    "fernando", "mohammad", "mohith", "durvish", "nistha", "ritwik",
}

# Deterministic clarify template (v2.1 UnifiedRouter CLARIFY family). Abstention is BUILT-but-OFF
# in Phase 1b, so this is reached only if a classifier ever returns CLARIFY directly.
_CLARIFY_MSG = (
    "I want to make sure I answer the right thing — could you rephrase or add a bit more detail? "
    "For example, name the department, person, or topic you mean."
)

FREE_MODE_SYSTEM_PROMPT = (
    "You are GSA Gateway (current version: Kavosh v2.0), NJIT's Graduate Student "
    "Association assistant. The student has switched to general chat mode. Answer helpfully "
    "and conversationally. You may answer questions beyond GSA topics, but "
    "periodically remind students you can also help with GSA events, funding, "
    "and campus resources."
)


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

        # ── Explicit "search njit for X" ──────────────────────────────────────
        # The user literally asked to go to the live njit.edu site, so honor it directly —
        # wins BEFORE the structured router AND the v2.1 router (they'd answer a different
        # question). This deterministic trigger must precede the ACT branch — review F2.
        explicit_topic = parse_explicit_live_search(clean_text)
        if explicit_topic is not None:
            return await self._answer_explicit_live(req, explicit_topic)

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
                decision = self.unified_router.decide(clean_text)
            except Exception:  # noqa: BLE001 - router must never break the answer path
                logger.debug("router-v21 decide failed (ignored)", exc_info=True)
            if decision is not None:
                if botcfg.ROUTER_V21_SHADOW:
                    log_shadow({"message": clean_text[:200],
                                "new_family": decision.family, "new_skill": decision.skill})
                elif decision.family != "COMMAND":
                    return await self._answer_decision(req, decision)
                # ACT + COMMAND → fall through to the legacy command/intent handling below

        # ── Structured retrieval (enumerate/filter/traverse/count) ────────────
        # Tried BEFORE intent detection on purpose: phrasings like "list all CS
        # faculty" or "who works on social network analysis" otherwise mis-classify
        # as statement/food/social. Returns None for anything not clearly structured,
        # so descriptive questions fall straight through to the unchanged RAG path.
        # GSA-MODE ONLY: in free (general chat) mode the user wants the general LLM,
        # NOT a GSA structured answer — skip structured so free mode isn't identical to GSA.
        mode = self.conversation_manager.get_mode(user_id) if self.conversation_manager else "gsa"
        if mode != "free":
            structured = await self._try_structured(clean_text)
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
                    "wider NJIT community's too. _(Current version: **Kavosh v2.0** — کاوش, \"exploration.\")_\n\n"
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
                    "NJIT community's too. You're talking to my current version, **Kavosh v2.0** "
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
                    "I'm **GSA Gateway** (current version: **Kavosh v2.0** — \"exploration\"), NJIT's Graduate "
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
        return await self._rag_pipeline(req, clean_text, intent)

    async def _try_structured(self, text: str) -> Optional[str]:
        """Answer enumerate/filter/traverse/count questions from structured DB queries
        (complete + deterministic), or return None to fall through to semantic RAG.

        The skill queries run on a fresh connection in a worker thread (no event-loop
        blocking). The deterministic facts text is the answer; the LLM only rephrases
        it, and we fall back to the facts verbatim if the LLM is down."""
        db_path = getattr(self.db, "db_path", None) if self.db else None
        if not db_path:
            return None
        # cheap pre-gate: open a connection only for structured-/person-looking questions.
        # Long messages with none of these cues skip it; short ones (<=4 words, likely a
        # name like "Guiling Wang") always pass so the entity layer can resolve them.
        low = text.lower()
        cues = (
            "who ", "who'", "which ", "list ", " all ", "how many", "department",
            "faculty", "professor", "prof", "works on", "work on", "working on",
            "research", "area", "studies", "studying", "specializ", "expert",
            "tell me about", "about ", "e-mail", "email", "office", "phone",
            "title", "dean", "chair", "director", "head of", "name", "show",
            "every", "any ", "contact", "reach")
        if not any(c in low for c in cues) and len(low.split()) > 4:
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
                return (facts, structured_answer.deterministic_suffix(result),
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
        facts, suffix, deterministic = ran
        return await self._compose_structured(text, facts, suffix, deterministic)

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

    async def _answer_decision(self, req: MessageRequest, decision) -> MessageResponse:
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
                return await self._rag_pipeline(req, text, INTENT_QUESTION)
            try:
                ran = await asyncio.to_thread(self._structured_from_route,
                                              decision.skill, decision.args)
            except Exception as exc:  # noqa: BLE001 - never break; fall to RAG
                logger.warning("router-v21 structured run errored, falling to RAG: %s", exc)
                ran = None
            if ran:
                facts, suffix, deterministic = ran
                return MessageResponse(
                    text=await self._compose_structured(text, facts, suffix, deterministic))
            return await self._rag_pipeline(req, text, INTENT_QUESTION)
        if fam == "RAG":
            rag_intent = INTENT_FOOD if decision.source == "food" else INTENT_QUESTION
            return await self._rag_pipeline(req, text, rag_intent)
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
        text = apply_headsup(live.text, topic)
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

    async def _rag_pipeline(
        self,
        req: MessageRequest,
        clean_text: str,
        intent: str,
        temperature: float = 0.3,
    ) -> MessageResponse:
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

            # Expand short/officer queries
            words = clean_text.split()
            core = clean_text.strip("?!.,").strip().lower()
            matched_officer = next(
                (name for name in _OFFICER_FIRST_NAMES if name in core.split() or core == name),
                None,
            )
            is_officer_query = matched_officer is not None
            search_query = clean_text
            contact_filter = None

            if is_officer_query:
                search_query = (
                    f"Who is {matched_officer.title()} at GSA NJIT? "
                    f"Contact information and role for {matched_officer.title()}"
                )
                contact_filter = "contact"
            elif self.ollama and len(words) <= 3 and intent not in (INTENT_FOOD, INTENT_SOCIAL):
                expanded = await self.ollama.expand_query(clean_text)
                if expanded and expanded.lower() != clean_text.lower():
                    search_query = expanded

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

            # KB miss -> live njit.edu fallback (grounded, extractive). Fires when there is no
            # usable KB chunk OR the best chunk's reranker relevance is below threshold. No-ops
            # without a Brave key; LIVE_ENABLED=0 disables it (kill-switch). See live_fallback.py.
            used_live = False
            attempted_live = False   # auto-fire ran this turn (regardless of result)
            is_canned_deflection = False   # tag-at-source: our own "no info" reply
            if botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY and self.ollama and self.retriever:
                relevance = self.retriever.top_relevance(clean_text, chunks) if chunks else None
                if (not chunks) or (relevance is not None and relevance < botcfg.LIVE_THRESHOLD):
                    attempted_live = True
                    live = await self.live_search(clean_text)   # single seam (provider wiring + gate)
                    if live is not None:
                        response_text = live.text
                        source_note = live.source_url
                        used_ai = True
                        used_live = True
                        logger.info("live njit.edu fallback answered from %s", live.source_url)

            # Generate
            if used_live:
                pass
            elif chunks and self.ollama:
                ai_resp = await self.ollama.generate_answer(
                    question=clean_text,
                    chunks=chunks,
                    conversation_history=history,
                    temperature=temperature,
                )
                if ai_resp:
                    response_text = ai_resp
                    source_note = _source_note_for(ai_resp, chunks)
                    used_ai = True
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
                response_text = (
                    "I wasn't able to find specific information about that "
                    "in the GSA knowledge base.\n\n"
                    "For accurate information, please:\n"
                    "- Visit the GSA office at Campus Center 110A (weekdays 11AM–5PM)\n"
                    "- Email us at gsa-pres@njit.edu\n"
                    "- Use /contact to find the right officer"
                )
                is_canned_deflection = True

            # Deflection offer (offer-only — NEVER auto-fire). Detect a confident deflection:
            # tag-at-source (the canned no-info branch above) OR a narrow phrase-match on the
            # composed-from-chunks answer. Match on the PRE-heads-up text so the heads-up
            # "confirm with <office>" line can't self-trigger. Suppressed when the feature is
            # off, when we already answered live, or when this turn already tried live and got
            # nothing (don't offer to redo a search that just failed).
            is_deflection = is_canned_deflection or (
                bool(chunks) and used_ai and not used_live
                and looks_like_deflection(response_text)
            )
            offer_live_search = bool(
                botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                and is_deflection and not used_live and not attempted_live
            )

            # High-stakes heads-up: we still answer, but for immigration/billing/funding
            # questions, tell the student to confirm with the authoritative office. Only when
            # we actually answered from chunks (not the "no info" deflection above) or from the
            # live njit.edu fallback.
            if chunks or used_live:
                response_text = apply_headsup(response_text, clean_text)

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
            )

        except Exception as exc:
            logger.error("MessageHandler._rag_pipeline error: %s", exc, exc_info=True)
            return MessageResponse(
                text=(
                    "I encountered an error processing your question. "
                    "Please try again or contact a GSA officer at gsa-pres@njit.edu"
                )
            )
