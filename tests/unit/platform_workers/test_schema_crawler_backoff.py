"""A warehouse that keeps failing must back off instead of being retried every tick.

With no SchemaCatalog rows a warehouse is always "due", so before this a wrong password
(the dev demo warehouse logged in as "admin" with the crawler_ro password) failed
authentication and logged a traceback every POLL_INTERVAL_SECONDS, forever.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.platform_workers.workers.schema_crawler import worker as sc

pytestmark = pytest.mark.unit

_WAREHOUSE: dict[str, Any] = {"tenant_id": "t", "warehouse_id": "w", "engine": "postgres", "config": {}}
_KEY = ("t", "w")


@pytest.fixture(autouse=True)
def _clean_backoff() -> Iterator[None]:
    sc._failure_backoff.clear()
    yield
    sc._failure_backoff.clear()


def _session_factory() -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    return session


_USABLE = {"h": 1}


def _crawl(*, conn_params: Any = _USABLE, crawl: Any = None) -> MagicMock:
    crawl_mock = MagicMock(side_effect=crawl)
    with (
        patch.object(sc, "get_active_warehouses", return_value=[_WAREHOUSE]),
        patch.object(sc, "_last_crawled_by_warehouse", return_value={}),
        patch.object(sc.metadata_dispatch, "connection_params", return_value=conn_params),
        patch.object(sc, "selected_table_names", return_value=None),
        patch.object(sc, "crawl_warehouse", crawl_mock),
    ):
        sc.crawl_all(_session_factory, MagicMock())
    return crawl_mock


def test_a_failing_warehouse_is_not_retried_on_the_next_tick() -> None:
    first = _crawl(crawl=RuntimeError("password authentication failed"))
    second = _crawl()

    first.assert_called_once()
    second.assert_not_called()


def test_the_delay_doubles_and_is_capped_at_the_crawl_interval() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    delays = []
    for _ in range(30):
        sc._record_failure(_KEY, now)
        delays.append((sc._failure_backoff[_KEY][1] - now).total_seconds())

    assert delays[:3] == [60, 120, 240]
    assert max(delays) == sc.CRAWL_INTERVAL_SECONDS


def test_an_unusable_config_backs_off_too() -> None:
    _crawl(conn_params=None)

    assert sc._in_backoff(_KEY, datetime.now(UTC))


def test_it_is_retried_once_the_backoff_expires_and_success_clears_it() -> None:
    sc._failure_backoff[_KEY] = (3, datetime.now(UTC) - timedelta(seconds=1))

    _crawl().assert_called_once()
    assert _KEY not in sc._failure_backoff
