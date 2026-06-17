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

logger = logging.getLogger(__name__)

_OFFICER_FIRST_NAMES = {
    "fernando", "mohammad", "mohith", "durvish", "nistha", "ritwik",
}

FREE_MODE_SYSTEM_PROMPT = (
    "You are GSA Gateway, the official AI assistant for NJIT's Graduate Student "
    "Association. The student has switched to general chat mode. Answer helpfully "
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
    ) -> None:
        self.retriever = retriever
        self.ollama = ollama
        self.conversation_manager = conversation_manager
        self.intent_detector = intent_detector
        self.db = db
        self.rate_limiter = rate_limiter
        self.kb = kb
        self.config = config

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

        # ── Structured retrieval (enumerate/filter/traverse/count) ────────────
        # Tried BEFORE intent detection on purpose: phrasings like "list all CS
        # faculty" or "who works on social network analysis" otherwise mis-classify
        # as statement/food/social. Returns None for anything not clearly structured,
        # so descriptive questions fall straight through to the unchanged RAG path.
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
                    "Welcome back! What else can I help you with?\n"
                    "_(Type 'clear' to start a new conversation)_"
                )
            else:
                text = (
                    "سلام · Hola · नमस्ते · 你好 · হ্যালো · ආයුබෝවන් · Olá · Merhaba · Hello\n"
                    "_Don't see your language? Ask Mohammad — he'll happily add it!_\n\n"
                    "Hi! I'm **GSA Gateway**, NJIT's Graduate Student Association assistant.\n\n"
                    "I can help you with:\n"
                    "- MMI Workshop series\n"
                    "- **GSA** events and announcements\n"
                    "- Travel awards and funding\n"
                    "- Club financial rules\n"
                    "- Officer contacts\n"
                    "- GSA constitution and policies\n"
                    "- Campus resources\n\n"
                    "Just ask me anything!"
                )
            return MessageResponse(text=text)

        if intent == INTENT_FAREWELL:
            vpa = self.kb.contacts.get("vp_academic_affairs") if self.kb else None
            vpa_name  = vpa.name  if vpa else "Mohammad Dindoost"
            vpa_email = vpa.email if vpa else "gsa-vpa@njit.edu"
            return MessageResponse(
                text=(
                    "خداحافظ · Adiós · अलविदा · 再见 · বিদায় · Tchau · Hoşçakal · Goodbye\n\n"
                    "It was great chatting! Feel free to come back anytime.\n\n"
                    f"For any academic questions or GSA matters, reach out to your "
                    f"VP Academic Affairs:\n"
                    f"**{vpa_name}** — {vpa_email} · md72@njit.edu"
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
                    "I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association.\n\n"
                    f"I'm powered by **{model_name}** — a local language model running on NJIT infrastructure, "
                    "not a cloud service. Unlike ChatGPT, I'm purpose-built for GSA: my answers come directly "
                    "from official GSA documents, policies, and contacts. I don't browse the internet or answer "
                    "general topics outside NJIT GSA.\n\n"
                    "Ask me about events, travel awards, club funding, officer contacts, or anything GSA-related!"
                )
            else:
                text = (
                    "I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association — "
                    "purpose-built to answer questions about GSA services, events, funding, and campus resources."
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
        # cheap pre-gate: only structured-looking questions open a connection
        low = text.lower()
        if not any(c in low for c in (
                "who ", "which ", "list ", " all ", "how many", "department",
                "faculty", "professor", "works on", "work on", "working on",
                "research", "area", "studies", "studying", "specializ", "expert")):
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
                return structured_answer.format_answer(structured_answer.run(conn, rt))
            finally:
                conn.close()

        try:
            facts = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - never break the message path; fall to RAG
            logger.warning("Structured retrieval errored, falling back to RAG: %s", exc)
            return None
        if not facts:
            return None
        if self.ollama:
            composed = await self.ollama.compose_from_rows(text, facts)
            if composed:
                return composed
        return facts

    async def retry_question(self, req: MessageRequest) -> MessageResponse:
        """Re-run RAG at temperature=0.7 for the 🔄 retry button.

        Skips rate limiting and intent detection — the original request already
        passed both.  Returns a fresh MessageResponse with a new question_id.
        """
        return await self._rag_pipeline(
            req, req.text.strip(), INTENT_QUESTION, temperature=0.7
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

            # Generate
            if chunks and self.ollama:
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

            # High-stakes heads-up: we still answer, but for immigration/billing/funding
            # questions, tell the student to confirm with the authoritative office. Only when
            # we actually answered from chunks (not the "no info" deflection above).
            if chunks:
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
            )

        except Exception as exc:
            logger.error("MessageHandler._rag_pipeline error: %s", exc, exc_info=True)
            return MessageResponse(
                text=(
                    "I encountered an error processing your question. "
                    "Please try again or contact a GSA officer at gsa-pres@njit.edu"
                )
            )
