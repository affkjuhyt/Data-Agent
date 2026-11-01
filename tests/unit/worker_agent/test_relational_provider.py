"""Relational provider preserves the existing scoped query boundary."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from libs.contracts.v1.provider import ProviderRequest
from services.worker_agent.providers.relational import RelationalProvider
from services.worker_agent.query.executor import QueryOutcome

pytestmark = [pytest.mark.unit]

ORG_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SPACE_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_ID = UUID("22222222-2222-2222-2222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-3333-3333-333333333333")
CALLER_ID = UUID("44444444-4444-4444-4444-444444444444")
RUN_ID = UUID("55555555-5555-5555-5555-555555555555")


def request() -> ProviderRequest:
    return ProviderRequest(
        provider="relational",
        org_id=ORG_ID,
        data_space_id=SPACE_ID,
        principal_id=PRINCIPAL_ID,
        source_id=SOURCE_ID,
        sql="SELECT amount FROM sales",
        caller_kind="question",
        caller_id=CALLER_ID,
    )


def source() -> SimpleNamespace:
    return SimpleNamespace(id=SOURCE_ID, org_id=ORG_ID, data_space_id=SPACE_ID)


@pytest.mark.asyncio
async def test_provider_passes_scope_to_run_query_and_maps_success(monkeypatch) -> None:
    calls = []

    async def fake_run_query(**kwargs):
        calls.append(kwargs)
        return QueryOutcome(
            query_run_id=RUN_ID,
            status="succeeded",
            columns=[{"name": "amount", "type": "numeric"}],
            rows=[["12.50"]],
            truncated=True,
            sql=request().sql,
        )

    monkeypatch.setattr("services.worker_agent.providers.relational.run_query", fake_run_query)
    provider = RelationalProvider(
        name="relational",
        secrets=object(),
        source_loader=lambda _request: source(),
    )

    result = await provider.execute(request())

    assert calls[0] == {
        "org_id": ORG_ID,
        "data_space_id": SPACE_ID,
        "principal_id": PRINCIPAL_ID,
        "caller_kind": "question",
        "caller_id": CALLER_ID,
        "source": source(),
        "sql": "SELECT amount FROM sales",
        "secrets": provider.secrets,
        "as_of": None,
    }
    assert result.row_count == 1
    assert result.rows == [{"amount": "12.50"}]
    assert result.truncated is True
    assert result.provenance is not None
    assert result.provenance.query_run_id == RUN_ID
    assert result.provenance.source_id == SOURCE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error", "warning_code"),
    [("timeout", "query timed out", "timeout"), ("failed", "query failed", "failed")],
)
async def test_provider_maps_failed_outcomes_without_rows(
    monkeypatch, status: str, error: str, warning_code: str
) -> None:
    async def fake_run_query(**kwargs):
        del kwargs
        return QueryOutcome(query_run_id=RUN_ID, status=status, error=error)

    monkeypatch.setattr("services.worker_agent.providers.relational.run_query", fake_run_query)
    provider = RelationalProvider(
        name="relational", secrets=object(), source_loader=lambda _request: source()
    )

    result = await provider.execute(request())

    assert result.rows == []
    assert result.provenance is None
    assert result.error == error
    assert result.warnings[0].code == warning_code
