"""Removing a table from a warehouse config.

selected_tables holds dicts ({name, schema, …} from the connect wizard, {id,
tableName, …} from the Knowledge Hub), and delete_config_table compared each of
them to the table id string — nothing ever matched, so the route answered ok,
bumped row_version and removed nothing; the table reappeared on reload. Each
table now has one id (_table_key) that the config listing sends and the delete
matches. update_locked is replaced by a driver that runs `mutate` on a row.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.kb_backend.operations import warehouse_ops

pytestmark = pytest.mark.unit

WAREHOUSE = "00000000-0000-0000-0000-0000000000a1"
CONFIG = "00000000-0000-0000-0000-0000000000c1"
TENANT = "a0000000-0000-0000-0000-000000000001"

_TABLES = [
    {"name": "orders", "schema": "sales", "rowCount": 10},
    {"id": "hub-7", "tableName": "users", "schema": "crm"},
    "legacy_table",
]


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What delete_config_table would write to the locked row."""
    writes: list[dict[str, Any]] = []

    async def no_tenant_check(*_: Any, **__: Any) -> None:
        return None

    async def fake_update_locked(
        postgres: Any, orm: Any, conditions: Any, mutate: Any, **_: Any
    ) -> uuid.UUID:
        writes.append(
            mutate(SimpleNamespace(config={"selected_tables": list(_TABLES), "host": "h"}))
        )
        return uuid.uuid4()

    monkeypatch.setattr(warehouse_ops, "_assert_tenant", no_tenant_check)
    monkeypatch.setattr(warehouse_ops, "update_locked", fake_update_locked)
    return writes


def _delete(table_id: str) -> dict[str, Any]:
    return asyncio.run(
        warehouse_ops.delete_config_table(None, WAREHOUSE, CONFIG, table_id, tenant_id=TENANT)
    )


def test_the_listing_gives_every_shape_of_table_an_id() -> None:
    cfg = warehouse_ops._to_wconfig(
        {"config_id": CONFIG, "version_number": 1, "config": {"selected_tables": _TABLES}}
    )

    assert [t["id"] for t in cfg["tables"]] == ["sales.orders", "hub-7", "legacy_table"]


@pytest.mark.parametrize(
    ("table_id", "left"),
    [
        ("sales.orders", ["hub-7", "legacy_table"]),
        ("hub-7", ["sales.orders", "legacy_table"]),
        ("legacy_table", ["sales.orders", "hub-7"]),
    ],
)
def test_deleting_by_the_listed_id_removes_exactly_that_table(
    written: list[dict[str, Any]], table_id: str, left: list[str]
) -> None:
    _delete(table_id)

    [change] = written
    assert [warehouse_ops._table_key(t) for t in change["config"]["selected_tables"]] == left
    assert change["config"]["host"] == "h"


def test_an_unknown_table_is_a_404_not_a_silent_ok(written: list[dict[str, Any]]) -> None:
    with pytest.raises(HTTPException) as exc:
        _delete("cfg-1-t-0")

    assert exc.value.status_code == 404
