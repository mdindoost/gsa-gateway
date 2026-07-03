from __future__ import annotations
import sys, time
from pathlib import Path
from autoeval.config import assert_env
from autoeval.models import KavoshObservation

REPO = Path("/home/md724/gsa-gateway")
sys.path.insert(0, str(REPO))

# Canned strings copied verbatim from bot/core/message_handler.py (:43-44, :58, :640).
_ABSTAIN_SUBSTRINGS = (
    "I wasn't able to find specific information about that",
    "I wasn't able to find a specific answer to that",
)
_CLARIFY_SUBSTRING = "I want to make sure I answer the right thing"

def detect_abstain(text: str) -> bool:
    return any(s in (text or "") for s in _ABSTAIN_SUBSTRINGS)

def detect_clarify(text: str) -> bool:
    return _CLARIFY_SUBSTRING in (text or "")

def resolved_key_for(decision) -> tuple[str | None, bool]:
    """Family-aware resolved key. Person skills -> entity_id; org-scoped skills -> str(org id).
    Org id is read from org_id OR parent_org_id (orgs_by_type uses parent_org_id) so the check
    is not tied to a drift-prone skill allowlist. slot_extracted flags LLM-slot-extraction routes."""
    args = getattr(decision, "args", {}) or {}
    slot = bool(args.get("_slot_extracted"))
    if args.get("entity_id"):
        return str(args["entity_id"]), slot
    for k in ("org_id", "parent_org_id"):
        if args.get(k) is not None:
            return str(args[k]), slot
    return None, slot

class KavoshRunner:
    def __init__(self, config):
        assert_env()  # fail fast if ROUTER_V21 / SHADOW / LIVE / SLOT_RECOVERY are wrong
        self._botcfg = None; self._asst = None; self._handler = None

    async def build(self, snapshot_path: str):
        from bot.config import config as botcfg
        botcfg.database_path = snapshot_path                 # retriever seam (assistant.py:114)
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        db = Database(snapshot_path)                          # combined mode; NO ops_db_path
        db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=botcfg.data_dir); kb.load()
        rl = RateLimiter(max_calls=10**9, period_seconds=1)
        self._botcfg = botcfg
        self._asst = await build_assistant(botcfg, db, kb, rl)
        self._handler = self._asst.message_handler
        assert self._handler.unified_router is not None, "ROUTER_V21 not active"

    async def warm(self) -> bool:
        """Pre-load the Ollama model into memory so the first real questions don't eat cold-start
        slot-extraction timeouts (generate_json_sync has only a 6s budget). One throwaway
        constrained-JSON call on the SAME model the router uses, with a generous timeout. Best-effort
        — returns True if the warm call succeeded, False otherwise; never raises."""
        import asyncio
        try:
            ol = getattr(self._asst, "ollama", None)
            if ol is None:
                return False
            from bot.services.ollama_client import generate_json_sync
            schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
            res = await asyncio.to_thread(
                generate_json_sync, "Reply with {\"ok\":true}.", "ready?", schema,
                base_url=ol.base_url, model=ol.model, timeout=180.0, num_predict=8)
            return res is not None
        except Exception:
            return False

    async def observe(self, question_text: str) -> KavoshObservation:
        from bot.core.message_handler import MessageRequest
        import uuid
        decision = self._handler.unified_router.decide(question_text)  # sync, side-effect-free
        key, slot = resolved_key_for(decision)
        t0 = time.time()
        resp = await self._handler.handle(MessageRequest(
            user_id=f"autoeval::{uuid.uuid4().hex}", text=question_text, platform="telegram"))
        latency = int((time.time() - t0) * 1000)
        text = (resp.text or "").strip()
        return KavoshObservation(
            answer_text=text, used_ai=bool(resp.used_ai),
            is_live=bool(getattr(resp, "is_live", False)),
            is_deep=bool(getattr(resp, "is_deep", False)), source_note=resp.source_note,
            family=getattr(decision, "family", None), skill=getattr(decision, "skill", None),
            resolved_key=key, slot_extracted=slot,
            is_abstain=detect_abstain(text), is_clarify=detect_clarify(text), latency_ms=latency)

    async def close(self):
        if self._asst and getattr(self._asst, "embedder", None):
            await self._asst.embedder.close()
        if self._asst and getattr(self._asst, "ollama", None):
            try: await self._asst.ollama.close()
            except Exception: pass
        try:
            db = getattr(self._handler, "db", None)
            if db is not None:
                db.close()
        except Exception:
            pass
