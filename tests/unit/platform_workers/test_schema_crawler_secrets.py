from __future__ import annotations

import pytest

from services.platform_workers.workers.schema_crawler import metadata

pytestmark = pytest.mark.unit


def test_connection_params_resolves_secret_ref_from_openbao(monkeypatch):
    seen: list[str] = []

    def resolve(path: str) -> str:
        seen.append(path)
        return "resolved-password"

    monkeypatch.setattr(metadata, "resolve_openbao_secret", resolve)
    monkeypatch.delenv("WAREHOUSE_PASSWORD__warehouse_1", raising=False)
    monkeypatch.delenv("POSTGRES_CRAWLER_PASSWORD", raising=False)

    params = metadata.connection_params(
        "postgres",
        {
            "connection": {
                "host": "warehouse",
                "database": "analytics",
                "username": "reader",
                "secret_ref": "warehouse/tenant-1/warehouse-1",
            }
        },
        "warehouse-1",
    )

    assert params is not None
    assert params["password"] == "resolved-password"
    assert seen == ["warehouse/tenant-1/warehouse-1"]


def test_connection_params_never_falls_back_to_password_environment(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_PASSWORD__warehouse_1", "plaintext")
    monkeypatch.setenv("POSTGRES_CRAWLER_PASSWORD", "plaintext")

    params = metadata.connection_params(
        "postgres",
        {"connection": {"host": "warehouse", "database": "analytics", "username": "reader"}},
        "warehouse-1",
    )

    assert params is None
