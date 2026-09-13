"""The RBAC matrix now lives in Postgres (RolePermissions), not in code.

What these cover is the behaviour a hardcoded registry got for free and a
database read does not: that an unreadable matrix is never mistaken for an
empty one, that role anchors are not mistaken for grants, and that the cache
which makes a per-request lookup affordable cannot serve a wildcard.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import pytest

from services.kb_backend.dependencies.permissions import (
    ANCHOR_RESOURCE,
    CATALOG,
    PERMISSIONS,
    PermissionStore,
    PermissionStoreUnavailable,
)

pytestmark = pytest.mark.unit

# (role_id, resource, action) as the table stores them.
ROWS = [
    ("PLATFORM_ADMIN",    "_role_anchor", "label"),
    ("AI_ENGINEER",       "_role_anchor", "label"),
    ("BUSINESS_OPERATOR", "_role_anchor", "label"),
    ("PLATFORM_ADMIN",    "documents",    "viewDocuments"),
    ("PLATFORM_ADMIN",    "data",         "deleteData"),
    ("PLATFORM_ADMIN",    "qdrant",       "toggleQdrant"),
    ("AI_ENGINEER",       "documents",    "viewDocuments"),
    ("AI_ENGINEER",       "qdrant",       "toggleQdrant"),
    ("BUSINESS_OPERATOR", "documents",    "viewDocuments"),
]

ADMIN_GRANTS = frozenset({"viewDocuments", "deleteData", "toggleQdrant"})


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _StubPostgres:
    """Serves the grant rows, minus whatever the query filtered out.

    It applies the anchor predicate itself rather than returning everything, so
    a store that forgot to filter shows up here as an extra permission instead
    of passing.
    """

    def __init__(self, rows: list[tuple[str, str, str]] | None = None) -> None:
        self.rows = ROWS if rows is None else rows
        self.queries = 0
        self.fail = False

    @asynccontextmanager
    async def get_platform_client(self):
        """RolePermissions is global; a tenant-scoped session there would be a lie."""
        self.queries += 1
        if self.fail:
            raise RuntimeError("postgres is down")
        yield self

    def get_client(self, tenant_id: str | None = None):
        raise AssertionError("the permission matrix must be read through get_platform_client")

    async def execute(self, stmt: Any) -> _Result:
        from sqlalchemy.dialects import postgresql
        rendered = str(stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        ))
        assert "RolePermissions" in rendered
        assert ANCHOR_RESOURCE in rendered, "anchors must be excluded in SQL, not in Python"
        return _Result([(role, action) for role, res, action in self.rows if res != ANCHOR_RESOURCE])


class TestGrants:
    def test_role_holds_exactly_its_granted_permissions(self):
        pg, store = _StubPostgres(), PermissionStore()
        granted = asyncio.run(store.granted_to(pg, "AI_ENGINEER"))

        assert granted == frozenset({"viewDocuments", "toggleQdrant"})

    def test_anchor_rows_are_not_permissions(self):
        """The four fixed-UUID rows exist for KBData's foreign key. If they
        leaked in, every role would hold a permission called `label`."""
        pg, store = _StubPostgres(), PermissionStore()
        granted = asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        assert "label" not in granted
        assert granted == ADMIN_GRANTS

    def test_unknown_role_holds_nothing(self):
        pg, store = _StubPostgres(), PermissionStore()

        assert asyncio.run(store.granted_to(pg, "DATA_ANALYST")) == frozenset()
        assert asyncio.run(store.can(pg, "DATA_ANALYST", "viewDocuments")) is False

    def test_empty_role_is_denied_without_touching_postgres(self):
        """A caller with no role must not become a wildcard — and must not
        depend on the database being up in order to be denied."""
        pg, store = _StubPostgres(), PermissionStore()

        assert asyncio.run(store.can(pg, "", "viewDocuments")) is False
        assert pg.queries == 0

    def test_can_is_any_not_all(self):
        """A route naming several permissions asks whether the caller may act
        by ANY of them — matching the registry this replaced."""
        pg, store = _StubPostgres(), PermissionStore()

        assert asyncio.run(store.can(pg, "AI_ENGINEER", "deleteData", "toggleQdrant")) is True
        assert asyncio.run(store.can(pg, "AI_ENGINEER", "deleteData", "addData")) is False

    def test_can_with_no_permission_names_is_false(self):
        pg, store = _StubPostgres(), PermissionStore()

        assert asyncio.run(store.can(pg, "PLATFORM_ADMIN")) is False


class TestPermissionsFor:
    def test_reports_the_whole_catalogue_not_just_the_grants(self):
        """/whoami's consumers switch UI off a False as much as on a True, so
        an ungranted permission has to be present-and-false, not absent."""
        pg, store = _StubPostgres(), PermissionStore()
        perms = asyncio.run(store.permissions_for(pg, "BUSINESS_OPERATOR"))

        assert set(perms) == PERMISSIONS
        assert perms["viewDocuments"] is True
        assert perms["deleteData"] is False
        assert perms["toggleQdrant"] is False

    def test_the_catalogue_is_what_is_reported_not_what_the_table_holds(self):
        """A permission no row mentions still has to appear as False — keying
        the response off the table would hide a control instead of disabling
        it, and the frontend cannot tell those apart."""
        pg, store = _StubPostgres(rows=[("PLATFORM_ADMIN", "documents", "viewDocuments")]), PermissionStore()
        perms = asyncio.run(store.permissions_for(pg, "PLATFORM_ADMIN"))

        assert set(perms) == PERMISSIONS
        assert perms["addWarehouse"] is False

    def test_unknown_role_gets_every_permission_false(self):
        pg, store = _StubPostgres(), PermissionStore()
        perms = asyncio.run(store.permissions_for(pg, "DATA_ANALYST"))

        assert set(perms) == PERMISSIONS
        assert all(v is False for v in perms.values())


class TestTheCatalogueGatesTheTable:
    def test_a_row_naming_an_uncatalogued_permission_is_ignored(self):
        """The trace a misspelt seed row leaves. It cannot authorise anything
        either way — no route asks for it — but it must not be reported as a
        grant, or someone reads /whoami and believes it applied."""
        pg = _StubPostgres(rows=ROWS + [("BUSINESS_OPERATOR", "data", "deleteDatum")])
        store = PermissionStore()

        granted = asyncio.run(store.granted_to(pg, "BUSINESS_OPERATOR"))

        assert "deleteDatum" not in granted
        assert granted == frozenset({"viewDocuments"})

    def test_an_uncatalogued_row_does_not_poison_the_rest_of_the_load(self):
        pg = _StubPostgres(rows=ROWS + [("PLATFORM_ADMIN", "data", "nonsense")])
        store = PermissionStore()

        assert asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN")) == ADMIN_GRANTS

    def test_it_is_logged_loudly_enough_to_find(self, caplog):
        pg = _StubPostgres(rows=ROWS + [("PLATFORM_ADMIN", "data", "deleteDatum")])
        store = PermissionStore()

        with caplog.at_level(logging.WARNING):
            asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        assert "deleteDatum" in caplog.text

    def test_every_catalogued_permission_names_a_resource(self):
        assert all(CATALOG.values()), "a permission with no resource is unfinished"
        assert set(CATALOG) == PERMISSIONS


class TestCaching:
    def test_second_lookup_is_served_from_cache(self):
        pg, store = _StubPostgres(), PermissionStore()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))
        asyncio.run(store.granted_to(pg, "AI_ENGINEER"))

        assert pg.queries == 1

    def test_invalidate_forces_a_reread(self):
        pg, store = _StubPostgres(), PermissionStore()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))
        store.invalidate()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        assert pg.queries == 2

    def test_a_grant_added_in_postgres_lands_after_invalidation(self):
        """The point of the move: a grant changes without a redeploy."""
        pg, store = _StubPostgres(), PermissionStore()
        assert asyncio.run(store.can(pg, "BUSINESS_OPERATOR", "deleteData")) is False

        pg.rows = ROWS + [("BUSINESS_OPERATOR", "data", "deleteData")]
        store.invalidate()

        assert asyncio.run(store.can(pg, "BUSINESS_OPERATOR", "deleteData")) is True

    def test_ttl_zero_rereads_every_time(self, monkeypatch):
        monkeypatch.setenv("KB_PERMISSION_CACHE_TTL", "0")
        pg, store = _StubPostgres(), PermissionStore()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        assert pg.queries == 2

    def test_concurrent_first_lookups_query_once(self):
        """The lock exists so a cold cache under load does not stampede."""
        pg, store = _StubPostgres(), PermissionStore()

        async def _race():
            return await asyncio.gather(*(
                store.granted_to(pg, "PLATFORM_ADMIN") for _ in range(8)
            ))

        results = asyncio.run(_race())

        assert pg.queries == 1
        assert all(r == ADMIN_GRANTS for r in results)

    def test_an_unparseable_ttl_falls_back_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("KB_PERMISSION_CACHE_TTL", "five minutes")
        pg, store = _StubPostgres(), PermissionStore()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        assert pg.queries == 1


class TestUnavailable:
    def test_no_snapshot_and_a_dead_database_raises_rather_than_denying(self):
        """The distinction the 503 rests on: we do not know the answer. Denying
        here would read as a policy decision and send an operator hunting for a
        grant that is already there."""
        pg, store = _StubPostgres(), PermissionStore()
        pg.fail = True

        with pytest.raises(PermissionStoreUnavailable):
            asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

    def test_a_dead_database_never_grants(self):
        pg, store = _StubPostgres(), PermissionStore()
        pg.fail = True

        with pytest.raises(PermissionStoreUnavailable):
            asyncio.run(store.can(pg, "PLATFORM_ADMIN", "deleteData"))

    def test_a_warm_cache_survives_the_database_going_down(self, monkeypatch):
        """Serving a stale matrix beats locking every user out of a running
        service — but only because it was read successfully at least once."""
        monkeypatch.setenv("KB_PERMISSION_CACHE_TTL", "0")
        pg, store = _StubPostgres(), PermissionStore()
        asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN"))

        pg.fail = True

        assert asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN")) == ADMIN_GRANTS

    def test_an_empty_table_is_not_the_same_as_an_unreadable_one(self):
        """An empty RolePermissions denies everything, quietly and correctly.
        The failure mode to avoid is the reverse, covered above."""
        pg, store = _StubPostgres(rows=[]), PermissionStore()
        perms = asyncio.run(store.permissions_for(pg, "PLATFORM_ADMIN"))

        assert asyncio.run(store.granted_to(pg, "PLATFORM_ADMIN")) == frozenset()
        assert set(perms) == PERMISSIONS and not any(perms.values())
