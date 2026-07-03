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

# Person-centric skills expose args["entity_id"]; org skills expose args["org_id"].
_ORG_SKILLS = {"people_in_org", "officers_in_org", "faculty_in_department",
               "orgs_by_type", "areas_in_org", "area_counts", "count_people_by_research_area",
               "top_people_by_metric", "people_by_research_area"}

def detect_abstain(text: str) -> bool:
    return any(s in (text or "") for s in _ABSTAIN_SUBSTRINGS)

def detect_clarify(text: str) -> bool:
    return _CLARIFY_SUBSTRING in (text or "")

def resolved_key_for(decision) -> tuple[str | None, bool]:
    """Family-aware resolved key. Person skills -> entity_id; org skills -> str(org_id).
    slot_extracted flags routes that went through LLM slot extraction (fidelity caveat)."""
    args = getattr(decision, "args", {}) or {}
    slot = bool(args.get("_slot_extracted"))
    skill = getattr(decision, "skill", None)
    if args.get("entity_id"):
        return str(args["entity_id"]), slot
    if skill in _ORG_SKILLS and args.get("org_id") is not None:
        return str(args["org_id"]), slot
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
