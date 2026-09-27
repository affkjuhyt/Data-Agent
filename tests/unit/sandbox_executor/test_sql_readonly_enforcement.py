"""Read-only asked of the ENGINE, not only inferred from the SQL text.

Two things already stood between an agent-written statement and a write, and
neither covers the case this suite is about:

  * sql_guard rejects what it RECOGNISES as a write. A write reached through a
    volatile function — through anything a SELECT is allowed to call — reads as
    a SELECT to it.
  * The platform's own DB roles carry no write grants. A DB Connector
    connection is the TENANT's credentials, which usually own the tables they
    read, so there is no grant to lean on there at all.

So each backend also tells its engine to refuse writes outright: a Postgres
``READ ONLY`` transaction, ClickHouse's ``readonly=2``. That request is what
these tests pin — the drivers are faked, so what is asserted is what the server
would be asked for, which is the whole of what this service controls.

Snowflake is deliberately absent from the write-refusal tests: it has no
read-only session or transaction mode to ask for (see snowflake_readonly's
module docstring). What it can be asked for is a cost bound, and that is
checked.
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")
_BACKENDS = ("postgres_readonly", "clickhouse_readonly", "snowflake_readonly")


@pytest.fixture(autouse=True)
def _sys_path():
    """Same isolation as test_postgres_readonly.py: the backends keep
    module-level connection pools, so each test gets a freshly imported one."""
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    for name in _BACKENDS:
        sys.modules.pop(name, None)
    yield
    for name in _BACKENDS:
        sys.modules.pop(name, None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


# ── Postgres ───────────────────────────────────────────────────────────────

def _pg_params(**overrides) -> dict:
    base = {"host": "localhost", "port": 5432, "user": "u1", "password": "pw1", "dbname": "db1"}
    base.update(overrides)
    return base


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection, readonly: bool) -> None:
        self._conn = conn
        self.readonly = readonly

    async def __aenter__(self) -> _FakeTransaction:
        self._conn.events.append("BEGIN READ ONLY" if self.readonly else "BEGIN")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._conn.events.append("COMMIT")
        return False


class _FakeCursor:
    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = list(batches)

    async def fetch(self, n: int) -> list[dict]:
        return self._batches.pop(0) if self._batches else []


class _FakeConnection:
    """Enough of an asyncpg.Connection for the read-only path, recording the
    ORDER of what happened — "a read-only transaction was opened" and "the
    query ran inside it" are different claims."""

    def __init__(self, batches: list[list[dict]] | None = None) -> None:
        self.events: list[str] = []
        self.transactions: list[_FakeTransaction] = []
        self.sql = ""
        self._batches = batches if batches is not None else [[{"a": 1}]]

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        pass

    def transaction(self, *, readonly: bool = False, **kwargs: object) -> _FakeTransaction:
        tx = _FakeTransaction(self, readonly)
        self.transactions.append(tx)
        return tx

    def cursor(self, sql: str, *args: object, **kwargs: object):
        self.events.append("QUERY")
        self.sql = sql

        async def _open() -> _FakeCursor:
            return _FakeCursor(self._batches)

        return _open()


def _patch_connect(monkeypatch, conn: _FakeConnection):
    import postgres_readonly as pr

    recorded: dict = {}

    async def _connect(**kwargs: object):
        recorded.update(kwargs)
        return conn

    monkeypatch.setattr(pr.asyncpg, "connect", _connect)
    return pr, recorded


@pytest.mark.unit
async def test_postgres_runs_the_query_inside_a_read_only_transaction(monkeypatch):
    conn = _FakeConnection()
    pr, _ = _patch_connect(monkeypatch, conn)

    result = await pr.execute_readonly_query("SELECT a FROM t", _pg_params())

    assert [tx.readonly for tx in conn.transactions] == [True]
    # Opened BEFORE the query and still open while it runs — a transaction
    # opened around nothing would satisfy a call count and enforce nothing.
    assert conn.events == ["BEGIN READ ONLY", "QUERY", "COMMIT"]
    assert result["rows"] == [{"a": 1}]


@pytest.mark.unit
async def test_postgres_connection_is_read_only_outside_the_transaction_too(monkeypatch):
    """The transaction is the enforcement; the startup parameter covers whatever
    else may ever run on this pooled connection."""
    pr, recorded = _patch_connect(monkeypatch, _FakeConnection())

    await pr.execute_readonly_query("SELECT a FROM t", _pg_params())

    settings = recorded["server_settings"]
    assert settings["default_transaction_read_only"] == "on"
    # Read-only bounds what a query may CHANGE, never what it may cost.
    assert int(settings["statement_timeout"]) > 0
    assert int(settings["idle_in_transaction_session_timeout"]) > 0


@pytest.mark.unit
async def test_postgres_never_opens_a_transaction_for_sql_the_guard_rejects(monkeypatch):
    import postgres_readonly as pr

    conn = _FakeConnection()

    async def _connect(**kwargs: object):
        return conn

    monkeypatch.setattr(pr.asyncpg, "connect", _connect)

    with pytest.raises(pr.PostgresQueryError):
        await pr.execute_readonly_query("DELETE FROM t", _pg_params())

    assert conn.events == []


# ── ClickHouse ─────────────────────────────────────────────────────────────

def _ch_params(**overrides) -> dict:
    base = {"host": "localhost", "port": 8123, "user": "u1", "password": "pw1", "database": "db1"}
    base.update(overrides)
    return base


class _ReadonlyRefusal(Exception):
    """What ClickHouse raises when a session may not change a setting."""

    code = 164

    def __init__(self, setting: str) -> None:
        super().__init__(
            f"Code: 164. DB::Exception: Cannot modify '{setting}' setting in readonly mode"
        )


class _FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[list]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


def _install_fake_clickhouse(
    monkeypatch, *, refuse: frozenset[str] = frozenset(), query_error: Exception | None = None,
) -> list[dict]:
    """Fake `clickhouse_connect`, refusing the named settings the way a server
    whose profile is already read-only does. Returns the settings of each
    get_client attempt, in order."""
    attempts: list[dict] = []

    class _Client:
        def __init__(self, settings: dict) -> None:
            self.settings = settings

        def query(self, sql: str) -> _FakeQueryResult:
            if query_error is not None:
                raise query_error
            return _FakeQueryResult(["a"], [[1]])

        def close(self) -> None:
            pass

    def get_client(**kwargs: object) -> _Client:
        settings = dict(kwargs.get("settings") or {})
        attempts.append(settings)
        blocked = sorted(refuse & set(settings))
        if blocked:
            raise _ReadonlyRefusal(blocked[0])
        return _Client(settings)

    module = types.ModuleType("clickhouse_connect")
    module.get_client = get_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "clickhouse_connect", module)
    return attempts


@pytest.mark.unit
async def test_clickhouse_asks_the_engine_for_readonly_mode(monkeypatch):
    attempts = _install_fake_clickhouse(monkeypatch)
    import clickhouse_readonly as ch

    result = await ch.execute_readonly_query("SELECT a FROM t", _ch_params())

    assert len(attempts) == 1, "a server that accepts the settings must not be retried"
    assert attempts[0]["readonly"] == 2
    assert attempts[0]["max_execution_time"] > 0
    assert result["rows"] == [{"a": 1}]


@pytest.mark.unit
async def test_clickhouse_stops_asking_when_the_server_is_already_stricter(monkeypatch):
    """A profile pinned to readonly=2 rejects any attempt to SET readonly. The
    refusal means the server enforces more than was asked for, so the ladder
    descends rather than failing a read that is already read-only."""
    attempts = _install_fake_clickhouse(monkeypatch, refuse=frozenset({"readonly"}))
    import clickhouse_readonly as ch

    result = await ch.execute_readonly_query("SELECT a FROM t", _ch_params())

    assert ["readonly" in a for a in attempts] == [True, True, False]
    assert attempts[-1]["max_execution_time"] > 0
    assert result["rows"] == [{"a": 1}]


@pytest.mark.unit
async def test_clickhouse_answers_even_when_every_setting_is_refused(monkeypatch):
    """readonly=1 refuses every setting change. That is the strictest posture of
    all, and must not read as a broken connection."""
    refuse = frozenset({
        "readonly", "max_execution_time", "max_result_rows",
        "max_result_bytes", "result_overflow_mode", "max_memory_usage",
    })
    attempts = _install_fake_clickhouse(monkeypatch, refuse=refuse)
    import clickhouse_readonly as ch

    result = await ch.execute_readonly_query("SELECT a FROM t", _ch_params())

    assert attempts[-1] == {}, "the last rung asks for nothing"
    assert result["rows"] == [{"a": 1}]


@pytest.mark.unit
async def test_clickhouse_does_not_retry_an_ordinary_query_failure(monkeypatch):
    """Only a refusal OF THE SETTINGS descends the ladder. A real query error
    retried with fewer settings would end up running under a weaker posture for
    a reason that had nothing to do with the settings."""
    attempts = _install_fake_clickhouse(
        monkeypatch,
        query_error=RuntimeError("Code: 60. DB::Exception: Table db1.t does not exist"),
    )
    import clickhouse_readonly as ch

    with pytest.raises(ch.ClickHouseQueryError):
        await ch.execute_readonly_query("SELECT a FROM t", _ch_params())

    assert len(attempts) == 1


# ── Snowflake ──────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_snowflake_session_carries_a_cost_bound(monkeypatch):
    """Snowflake has no read-only session to ask for — the role is the
    enforcement there. What it can be asked for is a statement timeout, and
    without one an agent's query runs to the account default (two days) on a
    warehouse billed by the second."""
    from unittest.mock import MagicMock

    import snowflake_readonly as sr

    conn = MagicMock()
    conn.is_closed.return_value = False
    conn.cursor.return_value.fetchall.return_value = []
    connect_spy = MagicMock(return_value=conn)
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    await sr.execute_readonly_query("SELECT a FROM t", {
        "account": "acct1", "user": "u1", "password": "pw1",
        "warehouse": "wh", "database": "db", "schema": "sch", "role": "role",
    })

    session_parameters = connect_spy.call_args.kwargs["session_parameters"]
    assert session_parameters["STATEMENT_TIMEOUT_IN_SECONDS"] > 0
