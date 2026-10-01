"""crawl_all must not leave its connection inside the transaction its reads opened.

The first statement starts a transaction. With no warehouse due, nothing in crawl_all
commits, so the worker used to sleep between ticks "idle in transaction", which blocks
DDL on the tables it read (and, through the lock queue, every later query on them).
With SQLAlchemy the read session is closed — rolling back and returning the connection
to the pool — when its `with` block ends; these tests pin that it ends before the tick
does and before any crawl starts.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.platform_workers.workers.schema_crawler import worker as sc

pytestmark = pytest.mark.unit


class _SessionFactory:
    """Records when each session it hands out is closed (its `with` block exits)."""

    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.opened = 0

    def __call__(self) -> MagicMock:
        self.opened += 1
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.side_effect = lambda *exc: self._order.append("close")
        return session


def test_the_read_transaction_is_ended_even_when_nothing_is_due() -> None:
    order: list[str] = []
    factory = _SessionFactory(order)
    with (
        patch.object(sc, "get_active_warehouses", return_value=[]),
        patch.object(sc, "_last_crawled_by_warehouse", return_value={}),
    ):
        sc.crawl_all(factory, MagicMock())
    assert factory.opened == 1 and order == ["close"]


def test_it_is_ended_before_any_crawl_starts() -> None:
    order: list[str] = []
    warehouse: dict[str, Any] = {"tenant_id": "t", "warehouse_id": "w", "engine": "postgres", "config": {}}
    with (
        patch.object(sc, "get_active_warehouses", return_value=[warehouse]),
        patch.object(sc, "_last_crawled_by_warehouse", return_value={}),
        patch.object(sc.metadata_dispatch, "connection_params", return_value={"h": 1}),
        patch.object(sc, "selected_table_names", return_value=None),
        patch.object(sc, "crawl_warehouse", side_effect=lambda *a, **k: order.append("crawl")),
    ):
        sc.crawl_all(_SessionFactory(order), MagicMock())
    assert order[0] == "close" and "crawl" in order
