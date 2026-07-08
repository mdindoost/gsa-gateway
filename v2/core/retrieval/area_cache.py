"""Best-effort persistent cache for LLM-verified area expansion (lives in the OPS DB, NOT the KB DB).
Opens its OWN short-lived writable connection so the read-path skills never write their caller-owned conn."""
from __future__ import annotations
import json, logging, os, sqlite3
from pathlib import Path
from v2.core.database.schema import get_ops_connection, create_ops_schema
logger = logging.getLogger(__name__)

def _ops_path() -> str:
    p = os.getenv("OPERATIONS_DB_PATH")
    if p:
        return p
    db = os.getenv("DATABASE_PATH", "./gsa_gateway.db")
    return str(Path(db).parent / "gsa_gateway_ops.db")

_SCHEMA_READY = False

def _conn() -> sqlite3.Connection:
    """Return a writable ops-DB connection. Ensures the schema exists exactly once
    per process (module reload resets the flag), then reuses the lighter
    get_ops_connection() for every subsequent call — this is a per-query hot path,
    so we must not re-run DDL/indexes/migrations on every access."""
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        create_ops_schema(_ops_path()).close()   # one-time ensure (and close that ensure-conn)
        _SCHEMA_READY = True
    return get_ops_connection(_ops_path())

def get(key: str) -> list[str] | None:
    c = None
    try:
        c = _conn()
        row = c.execute("SELECT tags_json FROM area_expand_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except Exception as e:  # noqa: BLE001 - best-effort
        logger.warning("area_cache.get failed: %s", e); return None
    finally:
        if c:
            c.close()

def put(key: str, tags: list[str]) -> None:
    c = None
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO area_expand_cache(key, tags_json) VALUES (?,?)",
                  (key, json.dumps(tags)))
        c.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.put failed: %s", e)
    finally:
        if c:
            c.close()

def get_blob(name: str) -> bytes | None:
    c = None
    try:
        c = _conn()
        row = c.execute("SELECT data FROM area_vocab_blob WHERE name=?", (name,)).fetchone()
        return bytes(row[0]) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.get_blob failed: %s", e); return None
    finally:
        if c:
            c.close()

def put_blob(name: str, data: bytes) -> None:
    c = None
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO area_vocab_blob(name, data) VALUES (?,?)", (name, data))
        c.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.put_blob failed: %s", e)
    finally:
        if c:
            c.close()
