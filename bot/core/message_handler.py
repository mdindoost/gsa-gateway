"""Platform-agnostic message handler — the shared brain for all connectors."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
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
from bot.core import identity
from bot.core.context_rewrite import resolve_query
from bot.core.deflection import looks_like_deflection
from bot.core.live_query import parse_explicit_live_search, LIVE_NOT_FOUND_MSG
from bot.core.live_fallback import maybe_answer_live, LiveAnswer, LiveLinks
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
    "- Or just ask me here — try naming the office, person, or topic you mean"
)

# Help text — a module constant so it is testable and can't drift. All-conversational: the ONLY real
# command is /qrcode (the v1 /events /contact /resources lookups were cut in the v1→v2 migration; QW-A10).
_HELP_RESPONSE = (
    "Here's how to use GSA Gateway:\n\n"
    "Just ask me your question naturally — no commands needed. For example:\n"
    '- "who is the GSA treasurer"\n'
    '- "CS faculty who work on AI"\n'
    '- "when is the travel award deadline"\n'
    '- "how do I book a room for my club"\n\n'
    "The one command I have is **/qrcode** (a branded QR to share the bot).\n\n"
    "Tips:\n"
    "- Ask follow-up questions naturally — I remember our conversation\n"
    "- Type 'clear' to reset our conversation"
)

# Deterministic clarify template (v2.1 UnifiedRouter CLARIFY family). Abstention is BUILT-but-OFF
# in Phase 1b, so this is reached only if a classifier ever returns CLARIFY directly.
_CLARIFY_MSG = (
    "I want to make sure I answer the right thing — could you rephrase or add a bit more detail? "
    "For example, name the department, person, or topic you mean."
)

# The RAG-pipeline hard-error reply (hoisted to a constant so the wording has a single source of
# truth — the eval harness keys abstain-classification off is_abstain, not this text).
_RAG_ERROR_RESPONSE = (
    "I encountered an error processing your question. "
    "Please try again or contact a GSA officer at gsa-pres@njit.edu"
)


def _live_links_text(urls: list[str]) -> str:
    """A1 off-target degrade: an honest 'closest pages' list when no live page ANSWERS the question.
    Verbatim Brave njit.edu URLs, no LLM — claims proximity, not answers."""
    lines = "\n".join(f"{i}. {u}" for i, u in enumerate(urls, 1))
    return ("I couldn't find a direct answer on NJIT's website. "
            "The closest pages I found:\n" + lines)

# The intents the legacy handle() treats as whole-message commands (mirrors the v2.1 command layer).
# Used only to label the LEGACY decision for shadow agreement (review F1).
_LEGACY_COMMAND_INTENTS = {
    INTENT_CLEAR_HISTORY, INTENT_GREETING, INTENT_FAREWELL, INTENT_THANKS,
    INTENT_HELP, INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE,
}

FREE_MODE_SYSTEM_PROMPT = (
    identity.persona_line() + " The student has switched to general chat mode. Answer helpfully "
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
    # QW-A16: granite4:tiny-h often emits a MALFORMED citation — "According to document
    # **[: Mathematical Sciences]**" — copying the [doc_id N: name] label but DROPPING the id, so the
    # doc_id subs above miss it. Strip the bracket WITH any flanking markdown emphasis FIRST (order
    # matters), then the now bracket-less "according to document" connector. The bracket pattern is
    # tight — only whitespace or "doc_id N" may precede the internal colon — so "[Note: …]",
    # "[Source: url]", "[10:30]" are UNTOUCHED (they carry other text before the colon).
    t = re.sub(r"[*_]{0,3}\[\s*(?:doc_?id\s*\d*)?\s*:\s*[^\]]*\][*_]{0,3}", "", t)
    t = re.sub(r"(?i)\baccording to (?:the )?document\b\s*[:,-]?\s*", "", t)
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


# QW-A4a — compose survival check. compose_from_rows truncates its output at num_predict; an uncapped
# roster can have the COUNT survive while the TAIL names vanish. This guard flags that so the caller
# keeps the complete Facts VERBATIM. Scoped to COUNTED rosters (the truncation-prone skills); short
# cards/prose are NOT second-guessed (return True) so the friendly greeting/phrasing survives. Errs
# toward verbatim-facts (safe, rule #2/#4) — over-triggering only costs a big list its rephrase.
_A4A_EMAIL_RX = re.compile(r"[\w.%+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_A4A_DIGIT_RUN_RX = re.compile(r"\d{3,}")
# counted-roster lead-in: a number immediately governing faculty/people/officer(s)/person — matches
# "has 30 faculty", "30 faculty work on", "has 5 officer(s)", "has N people". Real roster skills use
# MIXED separators (", " via _join for faculty/area rosters; "; " for officers/people_in_org).
_A4A_ROSTER_LEADIN_RX = re.compile(r"\b\d+\s+(?:faculty|people|officers?|persons?|departments?)\b", re.I)


def _a4a_norm(s: str) -> str:
    """Casefold + strip markdown emphasis + NFKD-fold diacritics — so 'José'/'Jose', '**Wicke**'/'wicke'
    compare equal (a composed answer may ASCII-fold or drop emphasis without dropping the name)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[*_`]", "", s).casefold()


def _compose_preserves_facts(facts: str, composed: str) -> bool:
    """True unless the composed answer DROPPED a fact from a counted-roster Facts. Checks (over a
    counted roster only): (a) every email survives, (b) every 3+-digit run survives, (c) every list
    item's tail token survives (a truncated tail loses its items' last tokens). Non-roster Facts →
    True (trust compose; protects the greeting). Over-triggers are safe: the caller keeps Facts verbatim.

    ALSO (affiliated-faculty): a home-vs-affiliated/joint distinction is load-bearing — if an
    "(affiliated)"/"(joint appointment)" marker in the Facts is dropped/reduced by compose (dropping it
    reads MORE authoritative, re-introducing the over-claim), keep verbatim Facts. This runs for ALL
    Facts — an entity_card never matches the roster lead-in below. Count-aware so a person with two
    marked edges can't false-pass when one marker survives and the other is dropped (Fable)."""
    cf_facts, cf_comp = facts.casefold(), composed.casefold()
    for marker in ("(affiliated)", "(joint appointment)"):
        if cf_comp.count(marker) < cf_facts.count(marker):
            return False
    if not _A4A_ROSTER_LEADIN_RX.search(facts):
        return True
    comp_cf = composed.casefold()
    for em in _A4A_EMAIL_RX.findall(facts):                 # (a) emails verbatim
        if em.casefold() not in comp_cf:
            return False
    for d in _A4A_DIGIT_RUN_RX.findall(facts):              # (b) 3+-digit runs (phones etc.)
        if d not in composed:
            return False
    comp_norm = _a4a_norm(composed)                         # (c) each list item's tail token
    body = facts.split(":", 1)[1] if ":" in facts else facts
    items = body.split(";") if ";" in body else body.split(",")
    for item in items:
        item = re.sub(r"\([^)]*\)", " ", item)             # drop parenthetical (email/org)
        toks = re.findall(r"[A-Za-zÀ-ɏ]{3,}", item)
        # word-boundary match (not bare substring) so a dropped "Chen" can't false-pass on a surviving
        # "Cheng" (Fable note #2 — hardens the one dangerous direction). Truncation drops a contiguous
        # tail, so a full false-pass would need every dropped surname to collide — now even less likely.
        if toks and not re.search(rf"\b{re.escape(_a4a_norm(toks[-1]))}\b", comp_norm):
            return False
    return True


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
    is_abstain: bool = False          # a canned non-answer (deflect/clarify/miss/error), NOT an answer
    abstain_reason: Optional[str] = None  # set iff is_abstain: gate1|clarify|live-miss|gate-abstain|
    #                       kb-miss|error|resume-error|ambiguous-antecedent (tag-at-source; never a heuristic)


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
                        _res = await self._resume_pending(_pending.options[_idx])
                        if _res is not None:
                            _resumed, _resumed_names = _res
                            self.conversation_manager.add_turn(user_id, "user", clean_text)
                            self.conversation_manager.add_turn(user_id, "assistant", _resumed[:500],
                                                               person_names=_resumed_names)  # A3 tag
                            return MessageResponse(text=_resumed)
                        # recognized but execution FAILED → graceful stop; NEVER fall through to route the token
                        sorry = "Sorry — I couldn't pull that up just now. Could you ask again?"
                        self.conversation_manager.add_turn(user_id, "user", clean_text)
                        self.conversation_manager.add_turn(user_id, "assistant", sorry)
                        return MessageResponse(text=sorry, is_abstain=True, abstain_reason="resume-error")
                    # _idx is None → unrecognized reply → pending already cleared → fall through, route normally
            except Exception:  # noqa: BLE001 - never break the answer path; fall through to routing
                logger.debug("followup resume pre-check failed (ignored)", exc_info=True)

        if mode != "free" and self.ollama and self.conversation_manager:
            _max_turns = getattr(self.config, "conversation_max_turns", 5)
            _hist = self.conversation_manager.get_history(user_id, max_turns=_max_turns)
            _rr = await resolve_query(clean_text, _hist, self.ollama)
            resolved_query = _rr.query
            # A3 layer 1: a bare "his/her" follow-up after a ≥2-person roster is an unresolvable
            # antecedent → CLARIFY instead of guessing one arbitrary name (honest-partial). v1 records
            # no pending; the user re-asks with a name and routes normally.
            if _rr.clarify_text is not None:
                return MessageResponse(text=_rr.clarify_text, is_abstain=True,
                                       abstain_reason=_rr.clarify_reason)

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
                return MessageResponse(text=_KB_MISS_RESPONSE, is_abstain=True, abstain_reason="gate1")

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
                # QW-A8: log the structured/KG answer so the connectors attach the 👍/👎/🔄 keyboard and
                # the tier is measurable. This LEGACY path is reached only when the v2.1 router returned
                # None/COMMAND (rare under ROUTER_V21=1), so the specific skill isn't in scope here → a
                # coarse "kg" tag. The primary path (_answer_decision) logs the granular "kg:{skill}".
                qid = self.db.log_question(
                    user_id=user_id, question=clean_text, matched_topic="kg",
                    confidence=100.0, guild_id=req.guild_id, platform=req.platform,
                ) if self.db else None
                return MessageResponse(text=structured, question_id=qid)

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
                    identity.welcome_back_line() + "\n"
                    "_(Type 'clear' to start a new conversation)_"
                )
            else:
                text = (
                    "سلام · Hola · नमस्ते · 你好 · হ্যালো · ආයුබෝවන් · Olá · Merhaba · Hello\n"
                    "_Don't see your language? Ask Mohammad — he'll happily add it!_\n\n"
                    "Hi! I'm **GSA Gateway** — NJIT's Graduate Student Association assistant, and the "
                    "wider NJIT community's too. " + identity.greeting_version_line() + "\n\n"
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
                    + identity.farewell_line() + "\n\n"
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
            return MessageResponse(text=_HELP_RESPONSE)

        if intent == INTENT_IDENTITY:
            # Self-facts render deterministically from bot/core/identity.py (single source of truth);
            # render_self picks a focused answer for narrow asks (who made you / run on / lineage /
            # limits) else the full render. Live model, so it can never drift from what's running.
            model_name = self.ollama.model if self.ollama else None
            return MessageResponse(text=identity.render_self(clean_text, model_name))

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
                # A3: person_names_of(result) computed HERE (the result dict dies with the thread);
                # threaded out so _register_and_record can tag the assistant turn.
                return (rt, facts, structured_answer.deterministic_suffix(result),
                        structured_answer.is_deterministic(result),
                        structured_answer.person_names_of(result))
            finally:
                conn.close()

        try:
            ran = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - never break the message path; fall to RAG
            logger.warning("Structured retrieval errored, falling back to RAG: %s", exc)
            return None
        if not ran:
            return None
        rt, facts, suffix, deterministic, person_names = ran
        composed = await self._compose_structured(text, facts, suffix, deterministic)
        if user_id is not None:                      # main :290 path → register + record
            self._register_and_record(user_id, clean_text or text, rt, composed, person_names)
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
            # QW-A4a: accept the compose ONLY if it didn't DROP a fact from a counted roster
            # (truncation at num_predict silently loses tail names/emails/digits); else keep the
            # complete Facts verbatim (rule #2/#4). Checked BEFORE the suffix append (suffix ∉ facts).
            if composed and _compose_preserves_facts(facts, composed):
                out = composed
        if suffix:
            out = f"{out}\n\n{suffix}"
        return out

    def _register_and_record(self, user_id, clean_text, rt, text, person_names=None) -> None:
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
            cm.add_turn(user_id=user_id, role="assistant", content=(text or "")[:500],
                        person_names=person_names or [])   # A3 tag-at-source
        except Exception:  # noqa: BLE001 - never break the answer path
            logger.debug("followup register_and_record failed (ignored)", exc_info=True)

    async def _resume_pending(self, option) -> "Optional[tuple[str, list]]":
        """Execute a pending option's structured resume, bypassing the router (deterministic).
        Returns (composed_text, person_names) or None on any failure (caller → graceful stop).
        person_names (A3) lets the resumed assistant turn be tagged at :333-337."""
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
        facts, suffix, deterministic, person_names = ran
        text = await self._compose_structured(option.label, facts, suffix, deterministic)
        return text, person_names

    def _structured_from_route(self, skill: str, args: dict):
        """SQL body for a DECIDED skill/args (no route() — the UnifiedRouter already resolved it).
        Thread target. Returns (facts, suffix, deterministic, person_names) or None (empty → caller
        falls to RAG). person_names (A3) is computed here where the result dict lives."""
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
                    structured_answer.is_deterministic(result),
                    structured_answer.person_names_of(result))
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
                facts, suffix, deterministic, person_names = ran
                text = await self._compose_structured(text, facts, suffix, deterministic)
                from v2.core.retrieval.router import Route
                self._register_and_record(req.user_id, req.text.strip(),
                                          Route(skill=decision.skill, args=dict(decision.args or {})),
                                          text, person_names)
                # QW-A8: log + attach a question_id so the KG tier gets the 👍/👎/🔄 keyboard and is
                # measurable (this is the LIVE primary path; the skill is in scope → granular tag).
                # A12 interaction (accepted + pinned in tests): the 🔄 button re-runs pure RAG at temp
                # 0.7, bypassing the router — a deterministic KG answer can be retried into a semantic
                # one. Kept per buttons-on-every-answer; a router-aware retry is deferred to A12.
                qid = self.db.log_question(
                    user_id=req.user_id, question=req.text, matched_topic=f"kg:{decision.skill}",
                    confidence=100.0, guild_id=req.guild_id, platform=req.platform,
                ) if self.db else None
                return MessageResponse(text=text, question_id=qid)
            return await self._rag_pipeline(req, text, INTENT_QUESTION, resolved_query=resolved_query)
        if fam == "RAG":
            rag_intent = INTENT_FOOD if decision.source == "food" else INTENT_QUESTION
            return await self._rag_pipeline(req, text, rag_intent, resolved_query=resolved_query)
        if fam == "LIVE":
            return await self._answer_explicit_live(req, text)
        if fam == "CLARIFY":
            return MessageResponse(text=_CLARIFY_MSG, is_abstain=True, abstain_reason="clarify")
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
        path can't drift. Returns a LiveAnswer, a LiveLinks (A1 off-target degrade, only under
        LIVE_RELEVANCE_GATE), or None (feature off / no key / no Ollama — a stale tapped button
        degrades gracefully instead of crashing)."""
        if not (botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY and self.ollama):
            return None
        # A1: LIVE_RELEVANCE_GATE turns on the answer-quality bundle — relevance-gate each extract,
        # fetch up to 3 pages (was 2), and degrade to top-3 links when none answers. Flag off = today.
        gate_on = botcfg.LIVE_RELEVANCE_GATE
        return await maybe_answer_live(
            question,
            search_fn=brave_search,
            fetch_fn=http_fetch,
            generate=lambda system, user: self.ollama.generate(user, system),
            relevance_ok=(self._live_relevance_ok if gate_on else None),
            degrade_links=gate_on,
            max_pages=(3 if gate_on else 2),
        )

    async def _live_relevance_ok(self, question: str, spans: list[str]) -> bool:
        """A1 relevance-gate: do these verbatim live spans actually ANSWER the question? Reuses the
        WS4 Gate-2 answerability judge (the same one KB answers pass) on the RAW spans. Answer-biased
        on ANY uncertainty (transport/parse/exception → KEEP; PARTIALLY_SUPPORTED → serve), so live is
        DROPPED only on a confident not-answered (the qual-exam→program-overview case)."""
        passages = [s for s in (spans or []) if s]
        if not passages or not self.ollama:
            return True
        try:
            sys_p, usr_p = gate2_prompt(question, passages)
            raw = await self.ollama.generate(
                prompt=usr_p, system=sys_p,
                options={"temperature": 0.0, "num_predict": 256,
                         "num_ctx": getattr(self.ollama, "num_ctx", None) or 8192}, fmt="json")
            if raw is None:                        # transport/empty → keep (QW-A2, never-withhold)
                return True
            v = parse_gate2(raw)
            if not v.parsed:                       # parse-fail → KEEP (B4 answer-bias; Fable R1)
                # decide_after_gate2 maps parsed=False → abstain (the France leak-guard for KB
                # compose). That guard does NOT transfer to live: the spans ARE verbatim njit.edu
                # page text, so keeping on a judge-malfunction is exactly flag-off behavior with
                # zero new fabrication risk — a DROP must require a CONFIDENT not-answered.
                return True
            outcome, _ = faith.decide_after_gate2(v.label, v.quote, passages, parsed=v.parsed)
            return outcome == "answer"
        except Exception:                          # gate fault → keep (never-withhold)
            logger.debug("live relevance gate faulted — keeping", exc_info=True)
            return True

    async def _answer_explicit_live(self, req: MessageRequest, topic: str) -> MessageResponse:
        """Run a direct live njit.edu search for an explicit 'search njit for X' request.
        Logged with a question_id (normal 👍/👎/🔄 buttons), but NO web-re-search offer (it
        just searched). Empty result → the shared 'found nothing' message."""
        live = await self.live_search(topic)
        if live is None:
            return MessageResponse(text=LIVE_NOT_FOUND_MSG, is_abstain=True, abstain_reason="live-miss")
        if isinstance(live, LiveLinks):            # A1: no page answered → honest top-3 links
            return MessageResponse(text=_live_links_text(live.urls), is_abstain=True,
                                   abstain_reason="live-offtarget")
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
        # QW-A6: the DETERMINISTIC checks (answer-type grounding) see the FULL text of the chunks the
        # model actually saw (the caller passes the FITTED chunks) — a grounded count/rate/money/date
        # past char 1200 on the deep-fallback tier (whole parent pages) must not be missed. Pure Python,
        # no prompt-size risk. (chunks here = the fitted set from OllamaClient.prefit; capped at 8.)
        full_passages = [(getattr(c, "text", "") or "") for c in chunks[:8]]
        outcome, reason = faith.assess_pre_gate2(question, answer, full_passages)
        if outcome == "gate2" and self.ollama:
            # Gate-2 is an LLM call — BOUND its per-passage window AND pass num_ctx. Without num_ctx the
            # request runs at the Ollama server default (~2k) and a long context front-truncates the
            # SYSTEM prompt (Gate-2's own instructions) → non-JSON → parsed=False → false-abstain, which
            # would INVERT this fix (Fable). The bounded window keeps the prompt well inside num_ctx.
            g2_passages = [p[:1200] for p in full_passages[:5]]
            sys_p, usr_p = gate2_prompt(question, g2_passages)
            raw = await self.ollama.generate(
                prompt=usr_p, system=sys_p,
                options={"temperature": 0.0, "num_predict": 256,
                         "num_ctx": getattr(self.ollama, "num_ctx", None) or 8192}, fmt="json",
            )
            # QW-A2: generate() returns None on a TRANSPORT failure (timeout/HTTP≠200/conn-error) OR an
            # EMPTY model response (it coerces "".strip() -> None). Either way the checker is UNREACHABLE
            # — NOT a deterministic out-of-domain garbage verdict — so KEEP the already-composed answer,
            # exactly like the gate-exception path (never-withhold hard line). A NON-empty unparseable
            # response stays non-None → parse_gate2 → still abstains (the France / out-of-domain guard).
            if raw is None:
                return True, "gate2-transport-keep"
            v = parse_gate2(raw)
            outcome, reason = faith.decide_after_gate2(v.label, v.quote, g2_passages, parsed=v.parsed)
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
            "- Or just ask me here — try naming the office, person, or topic you mean"
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

            # Retrieval is built from the context-resolved query (`base_q`); clean_text stays
            # original for compose/log/history.
            base_q = resolved_query or clean_text
            search_query = base_q
            # NOTE: two v1 GSA-framing hacks were REMOVED from this spot (2026-07-03):
            #  (1) the LLM short-query expander (thread B) — rewrote every short (<=3-word) query into
            #      a GSA-framed question, mis-framing non-GSA queries on the now university-wide corpus
            #      ("game development lab"/"computer science phd" -> GSA). Reverses the 2026-06-22 [SE4]
            #      "WRAP don't replace" decision (safe on a GSA-centric corpus; became the bias once
            #      the corpus went NJIT-wide). See …/specs/2026-07-03-remove-v1-expander-design.md.
            #  (2) is_officer_query — matched 6 hardcoded GSA officer FIRST names and rewrote to
            #      "Who is {Name} at GSA NJIT? Contact…" + a 'contact' source filter. A GSA/owner-
            #      privileging hack (hardcoded "mohammad" -> Dindoost despite 4 Mohammads in the KG)
            #      that a live measurement showed didn't even resolve the officers. Short queries now
            #      retrieve on base_q verbatim.
            # Optional future enhancement (logged, YAGNI): deterministic first-name resolution
            # (unique -> person, ambiguous -> person_disambig clarify) in the router.

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
                    source_type_filter=None,
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
            abstain_reason: Optional[str] = None   # tag-at-source reason when is_canned_deflection
            live_offtarget = False   # A1: live searched but no page answered → top-3-links degrade shown
            guard_answered = False   # A15b: the person-scope guard produced a terminal answer/abstain
            # base_q = the resolved/expanded query (main's contextual-rewrite); used for the
            # primary-miss signal, the office tier, and live — so a rewritten follow-up drives all
            # three. clean_text stays the original for compose/log/history.
            # A11: skip an unscored injected profile card at rank-0 so a person-topic query's
            # miss-signal reflects the real top chunk (else a false primary_miss → spurious fallback).
            relevance = (self.retriever.top_relevance(base_q, chunks,
                            skip_unscored=botcfg.MISS_SIGNAL_SKIP_UNSCORED)
                         if (self.retriever and chunks) else None)
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
                    and self.ollama and self.retriever and not botcfg.LIVE_OPTIN):
                # A1: LIVE_OPTIN suppresses the AUTO-fire — a genuine miss OFFERS instead (below),
                # never searches without consent. Flag off = today's auto-fire.
                attempted_live = True
                live = await self.live_search(base_q)   # LiveAnswer | LiveLinks | None
                if isinstance(live, LiveAnswer):
                    response_text = live.text
                    source_note = live.source_url
                    used_ai = True
                    used_live = True
                    logger.info("live njit.edu fallback answered from %s", live.source_url)
                elif isinstance(live, LiveLinks) and not chunks:
                    # Off-target with NO local material → the honest top-3-links deflection.
                    response_text = _live_links_text(live.urls)
                    is_canned_deflection = True
                    abstain_reason = "live-offtarget"
                    live_offtarget = True
                    logger.info("live njit.edu off-target — offered %d closest links", len(live.urls))
                # NB: LiveLinks WITH weak chunks present falls through untouched (Fable R2). Today
                # the same state (live→None) composes + WS4-gates those weak chunks, which can still
                # keep a good answer (B1: the gate judges weak answers, not the threshold). Turning
                # on LIVE_RELEVANCE_GATE must not newly convert an answerable turn into a deflection.

            # ── A15b person-scope guard (post-ladder, pre-compose) ──────────────────────────
            # On a person-LISTING query, a compose chunk WITHOUT an NJIT-Person entity_id (a seminar
            # abstract, an external-visitor page) must never stand in as the person answer. Keep only
            # stamped person chunks; if the settled pool has ZERO stamped person chunks, do NOT compose
            # from the pollution — degrade to live (the guard OWNS this attempt, since the ladder may
            # not have run when primary_miss was False — Fable R1), then honest-abstain. Fail-open (any
            # fault → chunks untouched). Skipped once live/office already answered. Only trims a
            # person-seeking pool, so non-person prose RAG is structurally untouched. Flag-gated.
            if (botcfg.PERSON_SCOPE_GUARD_ENABLED and chunks and not used_live and not live_offtarget
                    and not used_office):
                try:
                    from v2.core.retrieval.router import is_person_seeking
                    if is_person_seeking(base_q):
                        # Presence of entity_id == an active NJIT Person, by the crawler invariant:
                        # entity_id is stamped ONLY on NJIT Person chunks, reconcile drops departed
                        # people's KB, and retrieval filters is_active=1 — so a stale/external stamp
                        # can't reach this pool. A future ingester that stamps a non-person/external
                        # entity_id would silently admit pollution here (O1 — keep that invariant).
                        stamped = [c for c in chunks
                                   if (getattr(c, "metadata", {}) or {}).get("entity_id")]
                        if stamped:
                            if len(stamped) < len(chunks):
                                logger.info("person-scope guard: kept %d/%d NJIT-person chunks",
                                            len(stamped), len(chunks))
                            chunks = stamped
                        else:
                            # No confirmable NJIT person in a person-listing pool → never assert the
                            # unstamped pollution. Re-invoke live (guard-owned), then honest-abstain.
                            logger.info("person-scope guard: 0 NJIT-person chunks in a person-seeking "
                                        "pool — degrading (live→abstain)")
                            if (not attempted_live and botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                                    and self.ollama and self.retriever and not botcfg.LIVE_OPTIN):
                                attempted_live = True
                                live = await self.live_search(base_q)
                                if isinstance(live, LiveAnswer):
                                    response_text = live.text; source_note = live.source_url
                                    used_ai = True; used_live = True; guard_answered = True
                                elif isinstance(live, LiveLinks):
                                    response_text = _live_links_text(live.urls)
                                    is_canned_deflection = True; abstain_reason = "live-offtarget"
                                    live_offtarget = True; guard_answered = True
                            if not used_live and not live_offtarget:
                                response_text = self._useful_abstain(base_q, chunks)
                                source_note = None; used_ai = False
                                is_canned_deflection = True; abstain_reason = "person-scope-abstain"
                                guard_answered = True
                except Exception:
                    logger.exception("person-scope guard faulted; passing through (fail-open)")

            # Generate — compose FIRST, then run the post-generation faithfulness / answerability
            # gate (WS4) on the composed answer. The gate is deterministic answer-type grounding
            # (count/rate/money/date must carry a GROUNDED value of the expected type) + a
            # subjective-superlative guard + robust markdown-normalized quote grounding, with a
            # Gate-2 answerability verdict only for the non-typed factual residual. It replaces the
            # brittle pre-generation quote_grounded/fact_shaped gate (which false-abstained on
            # markdown — the Chrome-River bug — while leaking grounded-but-irrelevant fabrications).
            if used_live or live_offtarget or guard_answered:
                pass                                # live answered, off-target links, or guard abstained
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
                            # QW-A6: judge against the SAME fitted context the model saw (prefit), not
                            # raw chunks[:5][:1200] — else a grounded value past char 1200 false-abstains.
                            _fitted = self.ollama.prefit(compose_question, chunks, history) or chunks
                            _keep, _why = await self._faithfulness_gate(base_q, response_text, _fitted)
                        except Exception:
                            logger.exception("faithfulness-gate error; keeping composed answer")
                            _keep, _why = True, "gate-error-keep"
                        if not _keep:
                            # never-withhold: try an untried answering tier (live njit.edu) BEFORE
                            # abstaining — UNLESS opt-in (then we OFFER instead of auto-searching).
                            if (not attempted_live and botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                                    and self.ollama and self.retriever and not botcfg.LIVE_OPTIN):
                                attempted_live = True
                                live = await self.live_search(base_q)
                                if isinstance(live, LiveAnswer):
                                    response_text = live.text
                                    source_note = live.source_url
                                    used_live = True
                                    logger.info("faithfulness-gate->live answered from %s", live.source_url)
                                elif isinstance(live, LiveLinks):
                                    response_text = _live_links_text(live.urls)
                                    is_canned_deflection = True
                                    abstain_reason = "live-offtarget"
                                    live_offtarget = True
                            if not used_live and not live_offtarget:
                                response_text = self._useful_abstain(base_q, chunks)
                                source_note = None
                                used_ai = False
                                is_canned_deflection = True
                                abstain_reason = "gate-abstain"
                                # suppress the offer ONLY when we actually auto-tried live (not under
                                # opt-in — else the promised abstain+offer never renders — Fable B2).
                                if not botcfg.LIVE_OPTIN:
                                    attempted_live = True
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
                abstain_reason = "kb-miss"

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
            # A1/N2 — cross-platform opt-in. Telegram renders `offer_live_search` as a tappable
            # button (connector-side); Discord/GroupMe have no button, so when opt-in is on and we
            # would offer, append a one-line text hint pointing at the universal explicit-search
            # path. Platform-gated so Telegram doesn't get button + hint (double-offer).
            if (botcfg.LIVE_OPTIN and offer_live_search and req.platform != "telegram"):
                # O1: single quotes (not markdown bold) — GroupMe renders no markdown, so `**…**`
                # would show literal asterisks; quotes delimit the command cleanly on all platforms.
                response_text = (
                    f"{response_text}\n\n"
                    "Want me to check NJIT's website? Reply: "
                    f"'search njit for {clean_text.rstrip('?!. ')}'"
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
                is_abstain=is_canned_deflection,
                abstain_reason=abstain_reason if is_canned_deflection else None,
            )

        except Exception as exc:
            logger.error("MessageHandler._rag_pipeline error: %s", exc, exc_info=True)
            return MessageResponse(text=_RAG_ERROR_RESPONSE, is_abstain=True, abstain_reason="error")
