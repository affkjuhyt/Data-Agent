"""The audit bridge writes Keycloak auth events into "AuditLogs".

Regression: every insert failed on the dev cluster with
`null value in column "id" of relation "AuditLogs"` — the INSERT left `id` out
and the column had no server default — and the tenant lookup it relied on
(a "KeycloakRealmConfigs" row nobody writes) always came back empty, so the
next NOT NULL column would have failed the same way. One failing event also
kept the bridge from ever advancing its cursor.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from keycloak.exceptions import KeycloakGetError

from services.platform_workers.workers.audit import auth_events, bridge

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
USER = "6516da6c-bbaa-47ac-8c61-abfea8b9362d"

# "AuditLogs" columns that are NOT NULL with no server default in ff9ae8dcd7f7.
# id gains one in e4a6c8b0d2f1, but the insert must not depend on that
# migration having run.
_REQUIRED = {"id", "tenant_id", "actor", "actor_type", "action", "status"}


class _Cursor:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self._row: Any = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.executions.append((sql, params))
        self._row = (
            (1,) if sql.lstrip().startswith("SELECT") and params[0] in self.existing else None
        )

    def fetchone(self) -> Any:
        return self._row

    def inserts(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [(s, p) for s, p in self.executions if "INSERT" in s]


class _Conn:
    def __init__(self, cur: _Cursor) -> None:
        self.cur = cur
        self.commits = 0

    def cursor(self) -> _Cursor:
        return self.cur

    def commit(self) -> None:
        self.commits += 1


def _event(event_id: str = "ev-1", user_id: str | None = USER) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "REFRESH_TOKEN",
        "userId": user_id,
        "clientId": "aeroflow-frontend",
        "ipAddress": "10.42.0.1",
        "details": {"scope": "openid"},
        "time": 1_000,
    }


def _columns(sql: str) -> list[str]:
    match = re.search(r'INSERT INTO "AuditLogs"\s*\(([^)]*)\)', sql)
    assert match, sql
    return [c.strip() for c in match.group(1).split(",")]


class TestInsertEvent:
    def test_the_insert_supplies_every_not_null_column_including_id(self):
        cur = _Cursor()
        assert auth_events.insert_event(cur, _event(), TENANT) is True
        [(sql, params)] = cur.inserts()
        columns = _columns(sql)
        assert set(columns) >= _REQUIRED
        values = dict(zip(columns, params, strict=True))
        uuid.UUID(values["id"])
        assert values["tenant_id"] == TENANT
        assert values["source_event_id"] == "ev-1"

    def test_each_event_gets_its_own_id(self):
        cur = _Cursor()
        auth_events.insert_event(cur, _event("a"), TENANT)
        auth_events.insert_event(cur, _event("b"), TENANT)
        ids = {dict(zip(_columns(s), p, strict=True))["id"] for s, p in cur.inserts()}
        assert len(ids) == 2

    def test_an_event_already_written_is_not_written_again(self):
        cur = _Cursor(existing={"ev-1"})
        assert auth_events.insert_event(cur, _event(), TENANT) is False
        assert cur.inserts() == []

    def test_an_event_without_a_tenant_is_skipped_not_raised(self):
        cur = _Cursor()
        assert auth_events.insert_event(cur, _event(), None) is False
        assert cur.executions == []


class _Admin:
    def __init__(self, users: dict[str, dict[str, Any]]) -> None:
        self.users = users
        self.lookups: list[str] = []

    def get_user(self, user_id: str) -> dict[str, Any]:
        self.lookups.append(user_id)
        if user_id not in self.users:
            raise KeycloakGetError(error_message="User not found", response_code=404)
        return self.users[user_id]


class TestTenantFromKeycloak:
    def test_the_tenant_is_the_users_tenant_attribute(self):
        admin = _Admin({USER: {"attributes": {"tenant_id": [TENANT]}}})
        assert bridge.user_tenant(admin, USER) == TENANT

    def test_a_missing_user_or_attribute_has_no_tenant(self):
        admin = _Admin({"no-attr": {"attributes": {}}})
        assert bridge.user_tenant(admin, "gone") is None
        assert bridge.user_tenant(admin, "no-attr") is None
        assert bridge.user_tenant(admin, "") is None

    def test_a_keycloak_outage_is_not_mistaken_for_no_tenant(self):
        class _Down(_Admin):
            def get_user(self, user_id: str) -> dict[str, Any]:
                raise KeycloakGetError(error_message="unavailable", response_code=503)

        with pytest.raises(KeycloakGetError):
            bridge.user_tenant(_Down({}), USER)

    def test_a_batch_looks_each_user_up_once_and_skips_the_unattributable(self):
        admin = _Admin({USER: {"attributes": {"tenant_id": [TENANT]}}})
        cur = _Cursor()
        conn = _Conn(cur)
        events = [_event("a"), _event("b"), _event("c", user_id=None)]
        written = bridge.write_events(conn, events, lambda u: bridge.user_tenant(admin, u))
        assert written == 2
        assert admin.lookups == [USER]
        assert conn.commits == 1
