"""Integration: the offer turn registers a pending action + records history, on the LIVE
router path (ROUTER_V21=1). Uses the real assistant against the live DB, then cleans up its
analytics rows (mirrors scratchpad/repro_followup.py).

IMPORTANT (Fable #7): the assistant builds unified_router ONLY when botcfg.ROUTER_V21 is true
AT BUILD TIME. So we set the config-module attributes BEFORE build_assistant — os.environ at
import time is unreliable once bot.config is already imported by a conftest. botcfg.ROUTER_V21 /
FOLLOWUP_RESUME_ENABLED are read per-call, so setting the attrs is sufficient + correct."""
import asyncio, pytest


def _enable_flags():
    import bot.config as c
    c.ROUTER_V21 = True
    c.ROUTER_V21_SHADOW = False
    c.FOLLOWUP_RESUME_ENABLED = True


@pytest.mark.integration
def test_offer_registers_pending_and_history():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest
        _enable_flags()                                   # BEFORE build_assistant (Fable #7)

        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        U = "pytest_followup_user"
        cm = asst.message_handler.conversation_manager
        cm.clear_session(U)
        wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
        r = await asst.message_handler.handle(MessageRequest(user_id=U, text="who is the least cited professor in ywcc", platform="telegram"))
        db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
        assert "instead" in (r.text or "").lower()                 # the offer fired
        pa = cm.get_pending(U)
        assert pa is not None and pa.options[0].payload["skill"] == "top_people_by_metric"
        assert len(cm.get_history(U)) == 2                          # Bug 1 fixed: offer turn recorded
        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())


@pytest.mark.integration
def test_yes_resumes_the_metric_ranking():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest
        _enable_flags()                                   # BEFORE build_assistant (Fable #7)

        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        U = "pytest_followup_user2"; cm = asst.message_handler.conversation_manager; cm.clear_session(U)

        async def turn(t):
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            r = await asst.message_handler.handle(MessageRequest(user_id=U, text=t, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            return r

        await turn("who has the lowest citation in ywcc")
        r2 = await turn("yes")
        low = (r2.text or "").lower()
        assert "stem opt" not in low and "immigration" not in low     # NOT the old garbage
        assert "citation" in low or "cited" in low                    # a real ranked answer
        assert cm.get_pending(U) is None                              # consumed
        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())


@pytest.mark.integration
def test_disambig_offer_and_resume():
    async def run():
        from dotenv import load_dotenv; load_dotenv("/home/md724/gsa-gateway/.env")
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        from bot.core.message_handler import MessageRequest
        _enable_flags()
        db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
        asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
        h = asst.message_handler; cm = h.conversation_manager

        # find a surname shared by >=2 Person nodes
        surname = None
        for cand in ["wang", "chen", "kim", "lee", "zhang", "liu", "patel", "gupta", "singh", "li"]:
            n = db.conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND lower(name) LIKE ?",
                                (f"% {cand}",)).fetchone()[0]
            if n >= 2:
                surname = cand; break
        if surname is None:
            import pytest as _p; _p.skip("no ambiguous surname in live DB — disambig not exercisable here")

        U = "pytest_disambig"; cm.clear_session(U)
        async def turn(t):
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            r = await h.handle(MessageRequest(user_id=U, text=t, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            return r

        await turn(f"who is {surname}")
        pa = cm.get_pending(U)
        # If this assertion fails, ROUTER_V21 does NOT emit person_disambig → loudly defer G3-disambig.
        assert pa is not None and len(pa.options) >= 2, "disambig offer did not fire under ROUTER_V21"
        chosen = pa.options[0].label
        r2 = await turn(chosen)                      # select by full name
        assert (r2.text or "").strip() != "" and cm.get_pending(U) is None
        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())
