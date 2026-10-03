"""Saving a tenant's extraction policy for the first time.

A tenant without a KBExtractionPolicy row gets row_version "" from the GET, so
the client sends no If-Match; the route required one and answered 428, and the
create branch in update_extraction_policy was unreachable. If-Match is now
required only to edit an existing row. The postgres client is a fake.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.kb_backend.operations import policy_ops

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
VERSION = "00000000-0000-0000-0000-0000000000f1"


class _Postgres:
    def __init__(self, existing: bool) -> None:
        self.rows: list[dict[str, Any]] = (
            [{"policy_id": str(uuid.uuid4()), "custom_override": "old", "row_version": VERSION}]
            if existing
            else []
        )
        self.inserted: list[Any] = []

    async def read(self, request: Any) -> Any:
        return SimpleNamespace(code=200, data=list(self.rows))

    async def insert(self, model: Any) -> Any:
        self.inserted.append(model)
        self.rows.append(
            {
                "policy_id": str(uuid.uuid4()),
                "custom_override": model.custom_override,
                "row_version": VERSION,
            }
        )
        return SimpleNamespace(code=200, data={}, error=None)


def _save(postgres: _Postgres, expected_version: uuid.UUID | None) -> dict[str, Any]:
    return asyncio.run(
        policy_ops.update_extraction_policy(
            postgres, "only people and companies", TENANT, "steward", expected_version
        )
    )


def test_the_first_save_creates_the_policy_without_if_match() -> None:
    postgres = _Postgres(existing=False)

    result = _save(postgres, None)

    [created] = postgres.inserted
    assert created.custom_override == "only people and companies"
    assert result == {"status": "ok", "row_version": VERSION}


def test_editing_an_existing_policy_still_needs_if_match() -> None:
    postgres = _Postgres(existing=True)

    with pytest.raises(HTTPException) as exc:
        _save(postgres, None)

    assert exc.value.status_code == 428
    assert postgres.inserted == []
