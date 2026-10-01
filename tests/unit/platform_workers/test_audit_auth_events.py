"""The audit bridge writes Keycloak auth events into "AuditLogs".

Regression: every insert failed on the dev cluster with
`null value in column "id" of relation "AuditLogs"` — the INSERT left `id` out
and the column had no server default — and the tenant lookup it relied on
(a "KeycloakRealmConfigs" row nobody writes) always came back empty, so the
next NOT NULL column would have failed the same way. One failing event also
kept the bridge from ever advancing its cursor.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from keycloak.exceptions import KeycloakGetError

from services.platform_workers.workers.audit import auth_events, bridge

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
USER = "6516da6c-bbaa-47ac-8c61-abfea8b9362d"

class _Session:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.added: list[Any] = []
        self.commits = 0

    def scalar(self, _stmt: Any) -> Any:
        # The unit test only needs the duplicate branch.
        return object() if self.existing else None

    def add(self, row: Any) -> None:
        self.added.append(row)

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


class TestInsertEvent:
    def test_the_insert_supplies_every_not_null_column_including_id(self):
        session = _Session()
        assert auth_events.insert_event(session, _event(), TENANT) is True
        [row] = session.added
        assert isinstance(row.id, uuid.UUID)
        assert str(row.tenant_id) == TENANT
        assert row.actor == USER
        assert row.actor_type == "USER"
        assert row.action == "REFRESH_TOKEN"
        assert row.status == "SUCCESS"
        assert row.source_event_id == "ev-1"

    def test_each_event_gets_its_own_id(self):
        session = _Session()
        auth_events.insert_event(session, _event("a"), TENANT)
        auth_events.insert_event(session, _event("b"), TENANT)
        ids = {row.id for row in session.added}
        assert len(ids) == 2

    def test_an_event_already_written_is_not_written_again(self):
        session = _Session(existing={"ev-1"})
        assert auth_events.insert_event(session, _event(), TENANT) is False
        assert session.added == []

    def test_an_event_without_a_tenant_is_skipped_not_raised(self):
        session = _Session()
        assert auth_events.insert_event(session, _event(), None) is False
        assert session.added == []


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
        session = _Session()
        events = [_event("a"), _event("b"), _event("c", user_id=None)]
        written = bridge.write_events(session, events, lambda u: bridge.user_tenant(admin, u))
        assert written == 2
        assert admin.lookups == [USER]
        assert session.commits == 1
