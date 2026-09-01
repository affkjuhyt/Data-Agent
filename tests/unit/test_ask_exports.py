from __future__ import annotations

import io
import asyncio
import json
import pathlib
import sys
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

pytestmark = pytest.mark.unit


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": _Body(self.objects[(Bucket, Key)])}


def test_create_answer_export_stores_rows_payload_and_xlsx(monkeypatch):
    from services.workflow_runtime import answer_exports as mod

    fake = _FakeS3()
    monkeypatch.setattr(mod, "BUCKET", "answer-exports")
    monkeypatch.setattr(mod, "_s3_client", lambda: fake)
    monkeypatch.setattr(mod, "build_xlsx", lambda run, question: b"xlsx-bytes")

    run = {
        "id": "sql_1",
        "columns": ["region", "revenue"],
        "rows": [{"region": "Miền Bắc", "revenue": 612000000}],
        "sql": "SELECT region, revenue FROM fact_sales",
        "db_name": "dw",
        "db_type": "postgres",
        "generated_by": "semantic_layer",
        "metric": {"metric": "doanh_thu_thuan@3", "owner": "Finance"},
        "provenance": {"status": "fresh", "tables": [{"table": "fact_sales"}]},
    }

    meta = asyncio.run(mod.create_answer_export(run, tenant_id="tenant_1", question="Doanh thu?"))
    assert meta is not None
    result_id = meta["result_id"]
    prefix = f"tenant/tenant_1/ask-results/{result_id}"

    payload = json.loads(fake.objects[("answer-exports", f"{prefix}/payload.json")].decode("utf-8"))
    assert payload["rows"] == run["rows"]
    assert payload["source"]["sql"] == run["sql"]
    assert fake.objects[("answer-exports", f"{prefix}/answer.xlsx")] == b"xlsx-bytes"


def test_build_xlsx_contains_data_and_source_sheets():
    openpyxl = pytest.importorskip("openpyxl")
    from services.workflow_runtime import answer_exports as mod

    wb = openpyxl.load_workbook(io.BytesIO(mod.build_xlsx({
        "id": "sql_1",
        "columns": ["region", "revenue"],
        "rows": [{"region": "Miền Bắc", "revenue": 612000000}],
        "sql": "SELECT region, revenue FROM fact_sales",
    }, "Doanh thu?")))
    assert wb.sheetnames == ["Dữ liệu", "Nguồn"]
    assert wb["Dữ liệu"]["A1"].value == "region"
    assert wb["Nguồn"]["A1"].value == "Trường"


def test_result_rows_route_pages_by_authenticated_tenant(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.workflow_runtime.auth import get_current_user
    from services.workflow_runtime.routers import ask_exports

    async def fake_payload(tenant_id: str, result_id: str) -> dict[str, Any]:
        assert tenant_id == "tenant_1"
        assert result_id == "a" * 32
        return {
            "columns": ["id", "amount"],
            "rows": [{"id": "SO-2", "amount": 2}, {"id": "SO-1", "amount": 1}],
        }

    monkeypatch.setattr(ask_exports, "load_payload", fake_payload)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {"tenant_id": "tenant_1", "roles": ["platform-admin"]}
    app.include_router(ask_exports.router)

    res = TestClient(app).get(f"/api/ask/results/{'a' * 32}/rows", params={"limit": 1, "sort": "amount", "dir": "asc"})
    assert res.status_code == 200
    assert res.json() == {
        "result_id": "a" * 32,
        "columns": ["id", "amount"],
        "rows": [{"id": "SO-1", "amount": 1}],
        "total": 2,
        "offset": 0,
        "limit": 1,
        "has_more": True,
    }
