"""Tests for food query detection and food event retrieval."""

from datetime import date, timedelta

import pytest

from bot.services.food_detector import get_food_events, is_food_query, format_food_text


class TestFoodKeywords:
    def test_food_keywords_detected(self) -> None:
        assert is_food_query("free food") is True
        assert is_food_query("any food today?") is True
        assert is_food_query("who is the president?") is False
        assert is_food_query("hungry") is True
        assert is_food_query("pizza") is True

    def test_case_insensitive(self) -> None:
        assert is_food_query("FREE FOOD") is True
        assert is_food_query("Is There LUNCH?") is True

    def test_unrelated_queries_not_flagged(self) -> None:
        assert is_food_query("research funding") is False
        assert is_food_query("how do I join GSA?") is False
        assert is_food_query("office hours") is False


class TestGetFoodEvents:
    def test_get_food_events_returns_today(self, db) -> None:
        today = date.today().isoformat()
        db.add_event(
            "Test Coffee Hour", today, "10 AM", "Campus Center",
            "Free coffee and snacks for all students", "GSA", "", "food", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert any(e["name"] == "Test Coffee Hour" for e in events)

    def test_get_food_events_within_7_days(self, db) -> None:
        future = (date.today() + timedelta(days=5)).isoformat()
        db.add_event(
            "Food Event 5 Days", future, "12 PM", "Ballroom",
            "Lunch provided for all attendees", "GSA", "", "food", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert any(e["name"] == "Food Event 5 Days" for e in events)

    def test_get_food_events_excludes_old(self, db) -> None:
        past = (date.today() - timedelta(days=10)).isoformat()
        db.add_event(
            "Old Food Event", past, "12 PM", "Campus",
            "Free lunch was provided", "GSA", "", "food", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert not any(e["name"] == "Old Food Event" for e in events)

    def test_no_food_events_returns_empty_list(self, db) -> None:
        # Empty DB → get_food_events returns [] → ask.py shows friendly no-food message
        events = get_food_events(db=db, days_ahead=7)
        assert events == []

    def test_non_food_event_not_returned(self, db) -> None:
        future = (date.today() + timedelta(days=3)).isoformat()
        db.add_event(
            "Academic Workshop", future, "2 PM", "Room 101",
            "Research presentation on AI topics", "GSA", "", "academic", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert not any(e["name"] == "Academic Workshop" for e in events)

    def test_social_category_included(self, db) -> None:
        future = (date.today() + timedelta(days=2)).isoformat()
        db.add_event(
            "Social Mixer", future, "5 PM", "Pub",
            "Graduate student social gathering", "GSA", "", "social", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert any(e["name"] == "Social Mixer" for e in events)

    def test_food_keyword_in_description_caught(self, db) -> None:
        future = (date.today() + timedelta(days=1)).isoformat()
        db.add_event(
            "Research Symposium", future, "9 AM", "Ballroom",
            "Annual symposium. Lunch provided for all registered participants.",
            "GSA", "", "academic", 999,
        )
        events = get_food_events(db=db, days_ahead=7)
        assert any(e["name"] == "Research Symposium" for e in events)

    def test_results_sorted_by_date(self, db) -> None:
        day3 = (date.today() + timedelta(days=3)).isoformat()
        day1 = (date.today() + timedelta(days=1)).isoformat()
        db.add_event("Later Food Event",  day3, "5 PM", "Loc", "Free snacks", "GSA", "", "food", 999)
        db.add_event("Earlier Food Event", day1, "5 PM", "Loc", "Free snacks", "GSA", "", "food", 999)
        events = get_food_events(db=db, days_ahead=7)
        names = [e["name"] for e in events]
        assert names.index("Earlier Food Event") < names.index("Later Food Event")


class TestFormatFoodText:
    def test_format_food_text_today_and_upcoming(self) -> None:
        today = date.today().isoformat()
        events = [
            {"name": "Pizza Party", "date": today, "time": "5 PM", "location": "CC 110", "description": ""},
            {"name": "Ice Cream Social", "date": "2099-12-31", "time": "3 PM", "location": "Atrium", "description": ""},
        ]
        result = format_food_text(events)
        assert "pizza party" in result.lower()
        assert "ice cream social" in result.lower()
        assert "5 pm" in result.lower()

    def test_format_food_text_empty(self) -> None:
        result = format_food_text([])
        assert result == "" or "no" in result.lower()
