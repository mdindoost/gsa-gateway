# eval/processing_debt/bootstrap.py
"""Load the project .env into the pilot process so EMBEDDING_MODEL (and other serving config) match the
LIVE corpus. Without this, active_descriptor() defaults to nomic-768 while the Build-B corpus is 1024-d
Qwen → query embeddings silently mismatch → the KNN returns [] → garbage retrieval. Idempotent; never
overrides an already-set env var (load_dotenv's default is override=False, matching os.environ.setdefault).
"""
from __future__ import annotations
import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_loaded = False


def load_project_env() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    env = _REPO / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env)                       # override=False by default
    except Exception:
        if env.exists():
            for line in env.read_text().splitlines():
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
