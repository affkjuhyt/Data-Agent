from __future__ import annotations

import datetime as dt

import pytest

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBConnectionsInsert,
    KBStructuredImportJobInsert,
)
from libs.database_connector.postgres_connector import _prepare_insert

pytestmark = pytest.mark.unit


def test_insert_payload_keeps_python_values_for_sqlalchemy_columns() -> None:
    inserted = dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.UTC)
    prep = _prepare_insert(
        KBConnectionsInsert(
            tenant_id="9ff517c0-e492-4769-945e-f8d1c7be9cf0",
            name="Knowledge API",
            endpoint_url="https://kb.internal",
            inserted_at=inserted,
        )
    )

    assert prep.instance.tenant_id == "9ff517c0-e492-4769-945e-f8d1c7be9cf0"
    assert "inserted_at" not in prep.instance.__dict__


def test_insert_payload_does_not_json_encode_datetime_fields() -> None:
    reviewed_at = dt.datetime(2026, 10, 1, 13, 0, tzinfo=dt.UTC)
    prep = _prepare_insert(
        KBStructuredImportJobInsert(
            owner_id="2f56e12e-14fd-48cb-a72c-21053e84b956",
            reviewed_at=reviewed_at,
        )
    )

    assert prep.instance.reviewed_at is reviewed_at
