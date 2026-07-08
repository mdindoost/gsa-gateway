from __future__ import annotations

def _default_compose(conn, item_id, question):
    """Compose an answer from ONLY this chunk, using the real compose path. Returns text."""
    import asyncio
    row = conn.execute("SELECT title, content FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return ""
    facts = f"{row[0] or ''}: {row[1] or ''}"
    from bot.services.ollama_client import OllamaClient
    async def _run():
        return await OllamaClient().compose_from_rows(question, facts)
    try:
        return asyncio.run(_run()) or ""
    except Exception:
        return ""

def chunk_yields_fact(conn, item_id, question, fact, *, compose=None, entails_fn=None) -> bool:
    from eval.processing_debt.entailment import entails as _entails
    compose = compose or _default_compose
    entails_fn = entails_fn or _entails
    text = compose(conn, item_id, question)
    return bool(text and entails_fn(fact, text))
