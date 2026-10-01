"""crawl_all must not leave its connection inside the transaction its reads opened.

psycopg2 starts a transaction on the first statement. With no warehouse due, nothing in
crawl_all commits, so the worker used to sleep between ticks "idle in transaction", which
blocks DDL on the tables it read (and, through the lock queue, every later query on them).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.platform_workers.workers.schema_crawler import worker as sc

pytestmark = pytest.mark.unit


def test_the_read_transaction_is_ended_even_when_nothing_is_due() -> None:
    conn = MagicMock()
    with (
        patch.object(sc, "get_active_warehouses", return_value=[]),
        patch.object(sc, "_last_crawled_by_warehouse", return_value={}),
    ):
        sc.crawl_all(conn, MagicMock())
    conn.rollback.assert_called_once()


def test_it_is_ended_before_any_crawl_starts() -> None:
    conn = MagicMock()
    order: list[str] = []
    conn.rollback.side_effect = lambda: order.append("rollback")
    warehouse: dict[str, Any] = {"tenant_id": "t", "warehouse_id": "w", "engine": "postgres", "config": {}}
    with (
        patch.object(sc, "get_active_warehouses", return_value=[warehouse]),
        patch.object(sc, "_last_crawled_by_warehouse", return_value={}),
        patch.object(sc.metadata_dispatch, "connection_params", return_value={"h": 1}),
        patch.object(sc, "selected_table_names", return_value=None),
        patch.object(sc, "crawl_warehouse", side_effect=lambda *a, **k: order.append("crawl")),
    ):
        sc.crawl_all(conn, MagicMock())
    assert order[0] == "rollback" and "crawl" in order
