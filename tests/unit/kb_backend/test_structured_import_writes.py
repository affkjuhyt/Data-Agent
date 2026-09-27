"""Approving a structured import: one batch, one transaction, one lock.

The row-by-row insert held a pooled connection for the length of the
spreadsheet, and the catalogue row landed in a second transaction that a crash
could skip. Both are behaviours, not shapes, so these drive the endpoint and
inspect what reached the session.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
DATA_ID = "00000000-0000-0000-0000-0000000000aa"


class _Result:
    def first(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append((str(statement), params))
        return _Result()

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class _StubPostgres:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session
        self.inserts: list[Any] = []

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None):
        yield self.session

    async def insert(self, model: Any) -> Any:
        self.inserts.append(model)
        raise AssertionError("approve must not open a second transaction to catalogue the table")


class _StubMongoCollection:
    def __init__(self, rows: list[list[Any]], headers: list[str]) -> None:
        self.rows = rows
        self.headers = headers

    async def find_one(self, _query: Any) -> dict[str, Any]:
        return {"headers": self.headers, "rows": self.rows}


class _StubMongo:
    def __init__(self, collection: _StubMongoCollection) -> None:
        self.collection = collection

    def get_client(self) -> dict[str, _StubMongoCollection]:
        return {"kb_structured_staging": self.collection}


class _StubRequest:
    def __init__(self, postgres: Any, mongo: Any) -> None:
        state = type("State", (), {"postgres": postgres, "mongo": mongo})()
        self.app = type("App", (), {"state": state})()


class _StubJob:
    job_id = "00000000-0000-0000-0000-0000000000bb"
    status = "proposed"
    llm_proposed_schema: dict[str, Any] | None = None


def _approve(module, row_count: int, monkeypatch) -> tuple[_RecordingSession, dict[str, Any]]:
    session = _RecordingSession()
    postgres = _StubPostgres(session)
    rows = [[f"value-{index}"] for index in range(row_count)]
    mongo = _StubMongo(_StubMongoCollection(rows, ["Amount"]))

    async def fake_fetch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ingestion_mode": "structured_table", "extension": "xlsx"}

    async def fake_latest_job(*_args: Any, **_kwargs: Any) -> _StubJob:
        return _StubJob()

    monkeypatch.setattr(module, "_fetch_kbdata_or_404", fake_fetch)
    monkeypatch.setattr(module, "_latest_job", fake_latest_job)

    request = _StubRequest(postgres, mongo)
    body = module.ApproveRequest(schema={
        "table_name": "sales",
        "columns": [{"name": "amount", "type": "text", "nullable": True, "source_header": "Amount"}],
    })
    context = {"tenant_id": TENANT, "role_id": "role", "user_name": "tester"}
    result = asyncio.run(module.approve_structured_table(DATA_ID, body, request, context))
    return session, result


@pytest.fixture
def module():
    import services.kb_backend.router.knowledge.structured_table as structured_table
    return structured_table


def _row_writes(session: _RecordingSession) -> list[Any]:
    return [params for statement, params in session.statements if statement.startswith("INSERT INTO kb_structured")]


class TestRowsAreWrittenInBatches:
    def test_three_rows_cost_one_round_trip_not_three(self, module, monkeypatch):
        session, _ = _approve(module, row_count=3, monkeypatch=monkeypatch)
        writes = _row_writes(session)

        assert len(writes) == 1, f"expected one batched write, got {len(writes)}"
        assert isinstance(writes[0], list) and len(writes[0]) == 3

    def test_a_batch_boundary_splits_rather_than_growing(self, module, monkeypatch):
        monkeypatch.setattr(module, "_INSERT_BATCH_SIZE", 2)
        session, _ = _approve(module, row_count=5, monkeypatch=monkeypatch)
        writes = _row_writes(session)

        assert [len(batch) for batch in writes] == [2, 2, 1]

    def test_every_row_carries_the_caller_tenant(self, module, monkeypatch):
        session, _ = _approve(module, row_count=3, monkeypatch=monkeypatch)
        batch = _row_writes(session)[0]

        assert all(params["tenant_id"] == TENANT for params in batch)

    def test_an_oversized_import_is_refused(self, module, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr(module, "_MAX_IMPORT_ROWS", 2)

        with pytest.raises(HTTPException) as raised:
            _approve(module, row_count=3, monkeypatch=monkeypatch)
        assert raised.value.status_code == 422


class TestApproveIsOneTransaction:
    def test_a_single_commit_covers_the_whole_approval(self, module, monkeypatch):
        session, _ = _approve(module, row_count=2, monkeypatch=monkeypatch)
        assert session.commits == 1

    def test_the_catalogue_row_joins_the_same_transaction(self, module, monkeypatch):
        """_StubPostgres.insert() raises: a second transaction is the bug."""
        session, _ = _approve(module, row_count=2, monkeypatch=monkeypatch)
        assert [type(instance).__name__ for instance in session.added] == ["KBTableORM"]

    def test_the_job_and_the_tier_move_in_that_transaction_too(self, module, monkeypatch):
        session, _ = _approve(module, row_count=2, monkeypatch=monkeypatch)
        updates = [statement for statement, _ in session.statements if statement.startswith("UPDATE")]

        assert any("KBStructuredImportJob" in statement for statement in updates)
        assert any("KBData" in statement for statement in updates)


class TestRowLevelSecurityIsGated:
    def test_no_policy_is_created_while_the_gate_is_off(self, module, monkeypatch):
        monkeypatch.setattr(module, "_ENFORCE_ROW_LEVEL_SECURITY", False)
        session, _ = _approve(module, row_count=1, monkeypatch=monkeypatch)

        assert not [s for s, _ in session.statements if "ROW LEVEL SECURITY" in s]

    def test_turning_it_on_enables_and_forces_a_policy(self, module, monkeypatch):
        monkeypatch.setattr(module, "_ENFORCE_ROW_LEVEL_SECURITY", True)
        session, _ = _approve(module, row_count=1, monkeypatch=monkeypatch)
        statements = [s for s, _ in session.statements]

        assert any("ENABLE ROW LEVEL SECURITY" in s for s in statements)
        assert any("FORCE ROW LEVEL SECURITY" in s for s in statements)
        assert any("CREATE POLICY tenant_isolation" in s for s in statements)


class TestDocumentNameLock:
    def test_the_lock_is_taken_and_released_on_the_same_key(self):
        import services.kb_backend.router.knowledge.ingestion as ingestion
        session = _RecordingSession()
        postgres = _StubPostgres(session)

        async def run():
            async with ingestion._document_name_lock(postgres, TENANT, "Sales"):
                pass

        asyncio.run(run())
        locks = [(statement, params) for statement, params in session.statements if "advisory" in statement]

        assert len(locks) == 2
        assert "pg_advisory_lock" in locks[0][0] and "pg_advisory_unlock" in locks[1][0]
        assert locks[0][1] == locks[1][1]

    def test_it_is_released_even_when_the_upload_fails(self):
        import services.kb_backend.router.knowledge.ingestion as ingestion
        session = _RecordingSession()
        postgres = _StubPostgres(session)

        async def run():
            async with ingestion._document_name_lock(postgres, TENANT, "Sales"):
                raise RuntimeError("ingestion blew up")

        with pytest.raises(RuntimeError):
            asyncio.run(run())
        assert any("pg_advisory_unlock" in statement for statement, _ in session.statements)

    def test_different_documents_take_different_keys(self):
        import services.kb_backend.router.knowledge.ingestion as ingestion

        assert ingestion._document_name_key(TENANT, "Sales") != ingestion._document_name_key(TENANT, "Costs")
        assert ingestion._document_name_key(TENANT, "Sales") != ingestion._document_name_key(DATA_ID, "Sales")
        assert ingestion._document_name_key(TENANT, "Sales") == ingestion._document_name_key(TENANT, "Sales")

    def test_the_key_fits_a_postgres_bigint(self):
        import services.kb_backend.router.knowledge.ingestion as ingestion
        key = ingestion._document_name_key(TENANT, "Sales")

        assert -(2 ** 63) <= key < 2 ** 63
