"""Test-connection endpoint for the DB Connector canvas node — separate from
the actual node's execution path (nodes/sandbox/db_connector.py). Purely a
one-shot connectivity check so a user can verify host/port/database/user/
password BEFORE saving the workflow, without needing to run a full agent
conversation to discover a typo. Never persists anything."""
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel

router = APIRouter()


class TestConnectionRequest(BaseModel):
    host: str
    port: int = 5432
    database: str
    user: str
    password: str | None = None
    secret_id: str | None = None


@router.post("/api/db-connector/test-connection")
async def test_connection(body: TestConnectionRequest, x_tenant_id: str = Header(default="", alias="X-Tenant-Id")) -> dict[str, Any]:
    password = body.password
    if not password and body.secret_id:
        from warehouse_secrets import resolve_db_connector_password
        password = await resolve_db_connector_password(x_tenant_id, body.secret_id)
        if not password:
            return {"success": False, "error": "Không đọc được mật khẩu đã lưu cho kết nối này"}

    try:
        import asyncpg
        conn = await asyncpg.connect(
            host=body.host, port=body.port, user=body.user,
            password=password or "", database=body.database,
            timeout=5,
        )
        await conn.close()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
