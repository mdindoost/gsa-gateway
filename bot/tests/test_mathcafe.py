"""Tests for MathCafeService."""

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from bot.services.mathcafe import MathCafeService

SAMPLE_FACTS_YAML = {
    "metadata": {"total_facts": 1, "last_updated": "2026-05-26", "current_index": 0},
    "facts": [
        {
            "id": "mc_001",
            "title": "The Coin Flip Trap",
            "category": "math",
            "subcategory": "probability",
            "day_preference": "monday",
            "style": "detailed",
            "needs_image": True,
            "image_filename": "coin.png",
            "posted": False,
            "posted_date": None,
            "discussion": True,
            "reactions": ["1️⃣", "2️⃣"],
            "reaction_labels": {},
            "footer": "GSA MathCafe · Powered by GSA Gateway",
            "body": "Test body content.",
        }
    ],
}


def _make_service(tmp_path: Path, facts_data: dict | None = None) -> MathCafeService:
    facts_file = tmp_path / "facts.yml"
    data = facts_data if facts_data is not None else SAMPLE_FACTS_YAML
    with open(facts_file, "w") as fh:
        yaml.dump(data, fh, allow_unicode=True)
    bot = MagicMock()
    return MathCafeService(bot, facts_file=facts_file, images_dir=tmp_path / "images")


def test_facts_yml_loads_correctly():
    svc = MathCafeService(MagicMock())  # loads real facts.yml
    assert len(svc.facts) >= 1
    titles = [f["title"] for f in svc.facts]
    assert "The Coin Flip Trap" in titles
    mc001 = next(f for f in svc.facts if f["id"] == "mc_001")
    assert mc001["category"] == "math"


def test_get_next_fact_returns_unposted(tmp_path):
    svc = _make_service(tmp_path)
    svc.facts[0]["posted"] = True
    second = {
        "id": "mc_002",
        "title": "Test Puzzle",
        "category": "cs",
        "posted": False,
        "posted_date": None,
        "body": "Test.",
        "discussion": False,
        "reactions": [],
        "reaction_labels": {},
        "needs_image": False,
        "image_filename": None,
        "footer": "GSA MathCafe",
        "day_preference": "any",
        "style": "short",
        "subcategory": "",
    }
    svc.facts.append(second)
    result = svc.get_next_fact()
    assert result is not None
    assert result["id"] == "mc_002"


def test_all_facts_posted_resets_cycle(tmp_path):
    svc = _make_service(tmp_path)
    for fact in svc.facts:
        fact["posted"] = True
    svc.current_index = len(svc.facts)
    result = svc.get_next_fact()
    assert result is not None
    assert result["id"] == "mc_001"
    assert result["posted"] is False


def test_build_embed_has_correct_title(tmp_path):
    svc = _make_service(tmp_path)
    fact = svc.facts[0]
    embed = svc.build_embed(fact, datetime.date(2026, 5, 26))
    assert embed.title == "☕ GSA MathCafe"


def test_add_fact_increments_total(tmp_path):
    svc = _make_service(tmp_path)
    initial_count = len(svc.facts)

    import asyncio
    new_fact = asyncio.get_event_loop().run_until_complete(
        svc.add_fact(title="New Puzzle", body="Body text.", category="cs")
    )

    assert len(svc.facts) == initial_count + 1
    assert new_fact["id"] == f"mc_{initial_count + 1:03d}"
    assert new_fact["title"] == "New Puzzle"


def test_export_mathcafe_json_creates_file(tmp_path):
    svc = _make_service(tmp_path)
    svc.facts[0]["posted"] = True
    svc.facts[0]["posted_date"] = "2026-05-26"
    out = tmp_path / "mathcafe.json"
    svc.export_mathcafe_json(output_path=out)
    assert out.exists()
    with open(out) as fh:
        data = json.load(fh)
    assert "recent_facts" in data
    assert isinstance(data["recent_facts"], list)


def test_image_file_returns_none_when_missing(tmp_path):
    svc = _make_service(tmp_path)
    fact = {"needs_image": True, "image_filename": "nonexistent.png"}
    result = svc.get_image_file(fact)
    assert result is None
