"""Deterministic query correction (C+A): acronym dictionary + router-leader-rule support.
Spec §14. No LLM. Gated by QUERY_CORRECT_ENABLED (read at call time)."""
from __future__ import annotations
import os


def enabled() -> bool:
    return os.getenv("QUERY_CORRECT_ENABLED", "0") == "1"
