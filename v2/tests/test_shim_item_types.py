"""TDD RED → GREEN: V2RetrieverShim.retrieve must accept item_types (Gate0 C1).

Contract:
  1. `item_types` appears in the public async signature of V2RetrieverShim.retrieve.
  2. An explicit item_types kwarg is threaded through to the inner V2Retriever.retrieve call.
  3. source_type_filter fallback still works when item_types is not supplied.
  4. Calling shim.retrieve(query=..., item_types=[...]) does NOT raise TypeError.
"""
import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.integration.retriever_shim import V2RetrieverShim


def _make_shim():
    shim = V2RetrieverShim.__new__(V2RetrieverShim)
    shim.db_path = ":memory:"
    shim.embedder = MagicMock()
    shim.org_id = None
    shim.reranker = None
    # Semaphore must be created in the event loop, but __new__ skips __init__,
    # so create it here (same pattern as test_shim_query_vec.py patch approach).
    import asyncio as _asyncio
    shim._sem = _asyncio.Semaphore(1)
    return shim


def test_item_types_in_signature():
    """Signature contract: item_types param must exist on the public retrieve method."""
    params = inspect.signature(V2RetrieverShim.retrieve).parameters
    assert "item_types" in params, (
        "V2RetrieverShim.retrieve is missing the `item_types` parameter — "
        "message_handler's office tier will TypeError in prod"
    )


def test_item_types_forwarded_to_inner_retriever():
    """Plumbing contract: explicit item_types reaches the inner V2Retriever.retrieve call."""
    shim = _make_shim()
    captured = {}

    with patch("v2.integration.retriever_shim.get_connection", return_value=MagicMock()), \
         patch("v2.integration.retriever_shim.V2Retriever") as MockRetriever:
        MockRetriever.return_value.retrieve.side_effect = (
            lambda *a, **k: captured.update(k) or []
        )
        asyncio.run(shim.retrieve(query="office hours", item_types=["office_page"]))

    assert captured.get("item_types") == ["office_page"], (
        f"inner retriever received item_types={captured.get('item_types')!r}, expected ['office_page']"
    )


def test_item_types_does_not_raise_type_error():
    """Calling retrieve with item_types= must not blow up with TypeError (was the prod bug)."""
    shim = _make_shim()

    with patch("v2.integration.retriever_shim.get_connection", return_value=MagicMock()), \
         patch("v2.integration.retriever_shim.V2Retriever") as MockRetriever:
        MockRetriever.return_value.retrieve.return_value = []
        # This should not raise
        result = asyncio.run(shim.retrieve(query="parking info", item_types=["office_page"]))

    assert result == []


def test_source_type_filter_fallback_still_works():
    """Regression: source_type_filter still maps through _FILTER_MAP when item_types omitted."""
    shim = _make_shim()
    captured = {}

    with patch("v2.integration.retriever_shim.get_connection", return_value=MagicMock()), \
         patch("v2.integration.retriever_shim.V2Retriever") as MockRetriever:
        MockRetriever.return_value.retrieve.side_effect = (
            lambda *a, **k: captured.update(k) or []
        )
        asyncio.run(shim.retrieve(query="events this week", source_type_filter="event"))

    assert captured.get("item_types") == ["event_info"], (
        f"source_type_filter fallback broken, got item_types={captured.get('item_types')!r}"
    )


def test_item_types_wins_over_source_type_filter():
    """Explicit item_types overrides source_type_filter (item_types wins)."""
    shim = _make_shim()
    captured = {}

    with patch("v2.integration.retriever_shim.get_connection", return_value=MagicMock()), \
         patch("v2.integration.retriever_shim.V2Retriever") as MockRetriever:
        MockRetriever.return_value.retrieve.side_effect = (
            lambda *a, **k: captured.update(k) or []
        )
        asyncio.run(shim.retrieve(
            query="office page",
            item_types=["office_page"],
            source_type_filter="event",   # should be ignored
        ))

    assert captured.get("item_types") == ["office_page"]
