"""A3 integration (LIVE DB + real router): a structured roster answer tags its turn with the
person set (tag-at-source), and a bare "his/her" follow-up then CLARIFIES instead of guessing.
Proves the full wiring: _run/_structured_from_route → person_names_of → _register_and_record tag →
get_history → ambiguity_clarify gate → clarify MessageResponse. Cleans up its analytics rows.
Spec: docs/superpowers/specs/2026-07-04-a3-antecedent-ambiguity-design.md"""
import asyncio, pytest


def _enable_flags():
    import bot.config as c
    c.ROUTER_V21 = True
    c.ROUTER_V21_SHADOW = False
    c.FOLLOWUP_RESUME_ENABLED = True          # rosters enter history via _register_and_record
    c.ANTECEDENT_GUARD_ENABLED = True         # A3 gate + backstop


@pytest.mark.integration
def test_roster_then_pronoun_clarifies():
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
        U = "pytest_a3_user"; cm = asst.message_handler.conversation_manager; cm.clear_session(U)

        async def turn(t):
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            r = await asst.message_handler.handle(MessageRequest(user_id=U, text=t, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            return r

        # 1) a structured roster answer (GSA officers — several people) → tagged in history
        await turn("who are the gsa officers")
        hist = cm.get_history(U)
        assert hist, "roster turn should be recorded in history"
        last_asst = [h for h in hist if h["role"] == "assistant"][-1]
        assert len(last_asst.get("person_names") or []) >= 2, \
            f"roster turn should tag >=2 people, got {last_asst.get('person_names')}"

        # 2) a bare singular-pronoun follow-up → CLARIFY (no arbitrary pick)
        r2 = await turn("what is his email")
        assert r2.is_abstain and r2.abstain_reason == "ambiguous-antecedent", \
            f"expected ambiguous-antecedent clarify, got abstain={r2.is_abstain}/{r2.abstain_reason}"
        assert "which one" in (r2.text or "").lower()

        cm.clear_session(U)
        if asst.embedder: await asst.embedder.close()
        if asst.ollama: await asst.ollama.close()
        db.close()
    asyncio.run(run())
