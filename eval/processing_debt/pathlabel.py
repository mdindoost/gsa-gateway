# eval/processing_debt/pathlabel.py
"""Map a question to the pipeline path the production answer would take (Set-B stratification).
Loads the project .env at import so the in-process xray embeds with the live model (see bootstrap)."""
from __future__ import annotations
from eval.processing_debt.bootstrap import load_project_env

load_project_env()

_CONN = None


def _conn():
    global _CONN
    if _CONN is None:
        from eval.processing_debt.dbconn import get_ro_connection
        _CONN = get_ro_connection()
    return _CONN


def classify_path(xr) -> str:
    """DB-free core: XRay → path bucket. router-owned first, then KB-miss (live_fallback), else rag."""
    if xr.router_skill:
        return "router_hit"
    if xr.tier_primary_miss:
        return "live_fallback"     # KB miss → production would live-fallback / abstain
    return "rag"


def label_path(question: str) -> str:
    from eval.processing_debt.xray import xray
    return classify_path(xray(_conn(), question))
