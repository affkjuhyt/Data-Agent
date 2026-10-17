"""ClickHouse connection test: login, a read, and a refused write. Empty password is allowed."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from services.worker_agent import questions, source_tests
from services.worker_agent.query.secrets import StaticReader

pytestmark = [pytest.mark.unit]

ORG = uuid.uuid4()
REF = f"secret/tenants/{ORG}/sources/play"


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        kind="clickhouse",
        host="play.clickhouse.com",
        port=443,
        database_name="blogs",
        secret_ref=REF,
        conn_options=None,
    )


@pytest.fixture(autouse=True)
def _account():
    previous = questions._secrets
    questions._secrets = StaticReader({REF: {"user": "explorer", "password": ""}})
    yield
    questions._secrets = previous


async def test_https_playground_with_an_empty_password_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.host == "play.clickhouse.com"
        assert request.url.params["database"] == "blogs"
        seen.append(request.content.decode())
        sql = request.content.decode()
        if sql.startswith("CREATE"):
            return httpx.Response(500, text="readonly")
        if sql.startswith("SELECT version"):
            return httpx.Response(200, text="26.10.1.2435\n")
        return httpx.Response(200, text="1\n")

    async def _sql(
        client: httpx.AsyncClient, url: str, database: str | None, sql: str, auth: tuple[str, str]
    ) -> httpx.Response:
        assert auth == ("explorer", "")
        request = httpx.Request(
            "POST", url, params={"database": database} if database else None, content=sql
        )
        return handler(request)

    monkeypatch.setattr(source_tests, "_clickhouse_sql", _sql)
    passed, reason, version = await source_tests._check_clickhouse(_source(), ORG)  # type: ignore[arg-type]
    assert passed is True
    assert reason == "connection, login, catalog and read-only checks passed"
    assert version == "26.10.1.2435"
    assert any(sql.startswith("CREATE DATABASE") for sql in seen)


async def test_a_writable_account_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sql = request.content.decode()
        if sql.startswith("SELECT version"):
            return httpx.Response(200, text="26.9\n")
        return httpx.Response(200, text="ok\n")

    async def _sql(
        client: httpx.AsyncClient, url: str, database: str | None, sql: str, auth: tuple[str, str]
    ) -> httpx.Response:
        return handler(httpx.Request("POST", url, content=sql))

    monkeypatch.setattr(source_tests, "_clickhouse_sql", _sql)
    passed, reason, version = await source_tests._check_clickhouse(_source(), ORG)  # type: ignore[arg-type]
    assert passed is False
    assert reason == "the source account has write privileges"
    assert version == "26.9"


def test_a_host_with_a_path_is_refused() -> None:
    with pytest.raises(ValueError):
        source_tests._clickhouse_url("play.clickhouse.com/evil", 443)
