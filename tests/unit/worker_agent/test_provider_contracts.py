"""Provider boundary contracts are stable and JSON-safe."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from libs.contracts.v1.provider import ProviderRequest
from libs.contracts.v1.result import (
    ResultColumn,
    ResultEnvelope,
    ResultProvenance,
    ResultWarning,
)

pytestmark = [pytest.mark.unit]


def test_provider_request_carries_scope_and_query_context() -> None:
    request = ProviderRequest(
        provider="relational",
        org_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        data_space_id=UUID("11111111-1111-1111-1111-111111111111"),
        principal_id=None,
        source_id=UUID("22222222-2222-2222-2222-222222222222"),
        sql="SELECT 1",
        caller_kind="conversation",
        caller_id=UUID("33333333-3333-3333-3333-333333333333"),
        as_of=date(2026, 11, 1),
    )

    assert request.provider == "relational"
    assert request.as_of == date(2026, 11, 1)


def test_result_envelope_serializes_database_values_to_json() -> None:
    envelope = ResultEnvelope(
        columns=[ResultColumn(name="amount", type="numeric")],
        rows=[
            {
                "amount": Decimal("12.50"),
                "as_of": date(2026, 11, 1),
                "row_id": UUID("44444444-4444-4444-4444-444444444444"),
                "missing": None,
            }
        ],
        row_count=1,
        provenance=ResultProvenance(
            query_run_id=UUID("55555555-5555-5555-5555-555555555555"),
            source_id=UUID("22222222-2222-2222-2222-222222222222"),
            trust="governed",
            sql_sha256="abc123",
        ),
        warnings=[ResultWarning(code="partial", message="One column was unavailable")],
    )

    payload = envelope.to_json()

    assert payload["rows"][0] == {
        "amount": "12.50",
        "as_of": "2026-11-01",
        "row_id": "44444444-4444-4444-4444-444444444444",
        "missing": None,
    }
    assert payload["provenance"]["query_run_id"] == "55555555-5555-5555-5555-555555555555"


def test_result_envelope_supports_failed_execution_without_provenance() -> None:
    envelope = ResultEnvelope(
        columns=[],
        rows=[],
        row_count=0,
        error="query timed out",
        warnings=[ResultWarning(code="timeout", message="The provider timed out")],
    )

    assert envelope.error == "query timed out"
    assert envelope.provenance is None
    assert envelope.to_json()["warnings"] == [
        {"code": "timeout", "message": "The provider timed out"}
    ]
