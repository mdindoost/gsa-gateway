import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import bot.config as botcfg
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


class FakeChunk:
    def __init__(self, text, source_url, rel):
        self.text = text; self.source_url = source_url; self.relevance_score = rel
        self.item_id = 1; self.source_file = "eos__visitor-parking"; self.section_title = "Visitor Parking"


class FakeRetriever:
    def __init__(self, office_rel):
        self.office_rel = office_rel
    async def retrieve(self, query=None, conversation_history=None, source_type_filter=None, item_types=None):
        if item_types == ["office_page"]:
            return [FakeChunk("Visitor parking is in the Lock Street Deck.",
                              "https://www.njit.edu/parking/visitor-parking", self.office_rel)]
        return []                                   # curated miss -> primary_miss
    def top_relevance(self, q, chunks):
        return chunks[0].relevance_score if chunks else None


class FakeOllama:
    async def generate_answer(self, question, chunks, conversation_history=None, temperature=0.3):
        return f"Visitor parking is in the Lock Street Deck. (doc_id {chunks[0].item_id})"
    async def expand_query(self, t): return t


class FakeConv:
    def get_mode(self, uid): return "gsa"
    def get_history(self, uid, max_turns=5): return []
    def add_turn(self, **k): pass


def _handler(office_rel):
    h = MessageHandler(retriever=FakeRetriever(office_rel), ollama=FakeOllama(),
                       conversation_manager=FakeConv(), intent_detector=None, db=None,
                       rate_limiter=None, kb=None, config=SimpleNamespace(conversation_max_turns=5))
    h.live_calls = 0
    async def _no_live(text):
        h.live_calls += 1
        return None
    h.live_search = _no_live                        # record whether live was reached
    return h


def test_office_tier_answers_before_live_and_is_not_live(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "OFFICE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "x")
    h = _handler(office_rel=0.9)                     # office clears its floor
    req = MessageRequest(user_id="u", text="where do I park", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "where do I park", INTENT_QUESTION))
    assert "Lock Street Deck" in resp.text
    assert resp.source_note == "https://www.njit.edu/parking/visitor-parking"
    assert resp.is_live is False
    assert h.live_calls == 0                         # office preempted the live fallback


def test_office_below_floor_falls_through_to_live(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "OFFICE_THRESHOLD", 0.5)   # high floor
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "x")
    h = _handler(office_rel=0.2)                     # office BELOW the 0.5 floor
    req = MessageRequest(user_id="u", text="where do I park", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "where do I park", INTENT_QUESTION))
    assert h.live_calls == 1                         # office not adopted -> live attempted
