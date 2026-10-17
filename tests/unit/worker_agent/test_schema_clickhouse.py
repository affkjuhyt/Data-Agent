"""ClickHouse structure scan reads system.tables over HTTP, not the PostgreSQL catalog."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from services.worker_agent import questions, schema_scans
from services.worker_agent.query.secrets import StaticReader

pytestmark = [pytest.mark.unit]

ORG = uuid.uuid4()
REF = f"secret/tenants/{ORG}/sources/play"


def _source(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "kind": "clickhouse",
        "org_id": ORG,
        "host": "play.clickhouse.com",
        "port": 443,
        "database_name": "blogs",
        "scan_schemas": [],
        "secret_ref": REF,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _account():
    previous = questions._secrets
    questions._secrets = StaticReader({REF: {"user": "explorer", "password": ""}})
    yield
    questions._secrets = previous


def _rows(items: list[dict[str, object]]) -> str:
    return "".join(json.dumps(item) + "\n" for item in items)


async def test_an_empty_schema_list_scans_the_named_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _sql(
        client: httpx.AsyncClient, url: str, database: str | None, sql: str, auth: tuple[str, str]
    ) -> httpx.Response:
        assert client is not None
        assert url == "https://play.clickhouse.com:443/"
        assert database == "blogs"
        assert auth == ("explorer", "")
        seen.append(sql)
        if "system.tables" in sql:
            return httpx.Response(
                200,
                text=_rows(
                    [
                        {
                            "database": "blogs",
                            "name": "forex",
                            "engine": "ReplicatedMergeTree",
                            "total_rows": 3,
                        },
                        {
                            "database": "blogs",
                            "name": "rates",
                            "engine": "View",
                            "total_rows": None,
                        },
                    ]
                ),
            )
        return httpx.Response(
            200,
            text=_rows(
                [
                    {
                        "database": "blogs",
                        "table": "forex",
                        "name": "datetime",
                        "type": "DateTime64(3)",
                        "position": 1,
                        "is_in_primary_key": 1,
                    },
                    {
                        "database": "blogs",
                        "table": "forex",
                        "name": "bid",
                        "type": "Decimal(11, 5)",
                        "position": 2,
                        "is_in_primary_key": 0,
                    },
                ]
            ),
        )

    monkeypatch.setattr(schema_scans, "_clickhouse_sql", _sql)
    found = await schema_scans._scan_clickhouse(_source(), None)  # type: ignore[arg-type]
    assert "public" not in seen[0] and "'blogs'" in seen[0]
    forex, rates = found
    assert forex["schema_name"] == "blogs" and forex["table_name"] == "forex"
    assert forex["object_kind"] == "table" and forex["row_estimate"] == 3
    assert forex["foreign_keys"] == []
    assert forex["columns"] == [
        {
            "name": "datetime",
            "ordinal": 1,
            "data_type": "DateTime64(3)",
            "normalized_type": "timestamp",
            "is_nullable": False,
            "is_key": True,
        },
        {
            "name": "bid",
            "ordinal": 2,
            "data_type": "Decimal(11, 5)",
            "normalized_type": "decimal",
            "is_nullable": False,
            "is_key": False,
        },
    ]
    assert rates["object_kind"] == "view" and rates["columns"] == []


async def test_a_catalog_error_does_not_look_like_an_empty_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _sql(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(403, text="denied")

    monkeypatch.setattr(schema_scans, "_clickhouse_sql", _sql)
    with pytest.raises(RuntimeError, match="clickhouse catalog"):
        await schema_scans._scan_clickhouse(_source(), None)  # type: ignore[arg-type]
