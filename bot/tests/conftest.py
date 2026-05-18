"""Shared pytest fixtures for GSA Gateway tests."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.services.database import Database
from bot.services.knowledge_base import FAQEntry, KnowledgeBase
from bot.services.search import SearchService


# ── Database fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def db() -> Database:
    """In-memory SQLite database, fully initialised."""
    database = Database(":memory:")
    database.connect()
    database.init_tables()
    yield database
    database.close()


# ── Knowledge base fixtures ───────────────────────────────────────────────────

@pytest.fixture
def sample_faq() -> list[FAQEntry]:
    return [
        FAQEntry(
            question="What is the GSA?",
            answer="The Graduate Student Association is the representative body for all NJIT graduate students.",
        ),
        FAQEntry(
            question="How do I join the GSA?",
            answer="Membership is automatic for all enrolled graduate students. No sign-up needed.",
        ),
        FAQEntry(
            question="Are there funding opportunities for graduate students?",
            answer="Yes! Assistantships, fellowships like NSF GRFP, and NJIT Foundation Scholarships are available.",
        ),
        FAQEntry(
            question="How do I submit an initiative to GSA?",
            answer="Use the /initiative command in Discord to open the submission form.",
        ),
        FAQEntry(
            question="What mental health resources are available?",
            answer="The NJIT Counseling Center offers free confidential counseling for enrolled students.",
        ),
    ]


@pytest.fixture
def kb(sample_faq, tmp_path) -> KnowledgeBase:
    """KnowledgeBase pre-loaded with sample FAQ entries (no file I/O)."""
    knowledge_base = KnowledgeBase(data_dir=tmp_path)
    knowledge_base.faq_entries = list(sample_faq)
    return knowledge_base


@pytest.fixture
def search_svc(kb) -> SearchService:
    """SearchService backed by the sample knowledge base."""
    return SearchService(kb)


# ── Discord interaction mock ──────────────────────────────────────────────────

@pytest.fixture
def mock_interaction():
    """Minimal discord.Interaction mock with a guild member user."""
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 123456789
    interaction.guild_id = 987654321
    interaction.channel = MagicMock()
    interaction.channel.name = "gsa-general"
    return interaction
