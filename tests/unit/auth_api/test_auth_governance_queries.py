"""The auth_api read endpoints that returned 500 on the dev cluster (27/09/2026):
`/api/auth/secrets/pii-log` and `/audit-log` selected `key_rotations.rotated_at`,
a column the table never had (b8c9d0e1f2a3 creates `created_at`), and
`/api/auth/api-keys` used `psycopg2.extras` without importing it. Each handler
runs here against a fake connection, and every `kr.<column>` it selects is
checked against the columns the migration actually creates.
"""
from __future__ import annotations

import datetime
import re
from typing import Any

import pytest

from .._loader import REPO_ROOT, SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

_EXTRA = [SERVICES, SERVICES / "auth_api"]
governance = load_module_from_path(
    "governance_router", SERVICES / "auth_api" / "routers" / "governance.py", extra_syspath=_EXTRA,
)
api_keys = load_module_from_path(
    "api_keys_router", SERVICES / "auth_api" / "routers" / "api_keys.py", extra_syspath=_EXTRA,
)

_MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "b8c9d0e1f2a3_auth_api_secrets_vault_schema.py"


def _key_rotations_columns() -> set[str]:
    source = _MIGRATION.read_text(encoding="utf-8")
    block = source[source.index("'key_rotations',"):]
    block = block[: block.index("sa.CheckConstraint")]
    return set(re.findall(r"sa\.Column\('([a-z_]+)'", block))


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.statements.append(sql)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._conn.rows


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    def cursor(self, cursor_factory: Any = None) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        pass


_NOW = datetime.datetime(2026, 9, 27, tzinfo=datetime.timezone.utc)
_ROTATION = {
    "id": "r1", "triggered_by": "REVEAL", "actor_id": "u1", "old_version": 1, "new_version": 2,
    "status": "SUCCESS", "error": None, "access_reason": "audit", "created_at": _NOW,
    "key_name": "k", "key_type": "HMAC_KEY",
}
_ADMIN = {"user_id": "u1", "roles": ["platform-admin"], "tenant_id": "t1"}


@pytest.mark.parametrize("handler", ["secrets_pii_log", "secrets_audit_log"])
def test_rotation_logs_select_only_real_columns(monkeypatch: pytest.MonkeyPatch, handler: str) -> None:
    conn = _Conn([_ROTATION])
    monkeypatch.setattr(governance, "get_db", lambda: conn)

    result = getattr(governance, handler)(x_tenant_id="t1", user=_ADMIN)

    selected = set(re.findall(r"\bkr\.([a-z_]+)", conn.statements[0]))
    assert selected <= _key_rotations_columns(), f"not in key_rotations: {selected - _key_rotations_columns()}"
    assert result[0]["time"] == _NOW.isoformat()


def test_list_api_keys_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn([])
    monkeypatch.setattr(api_keys, "get_db", lambda: conn)

    assert api_keys.list_api_keys(x_tenant_id="t1", user=_ADMIN) == []
