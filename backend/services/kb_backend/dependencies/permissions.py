import asyncio
import logging
import os
import time
from typing import Any

from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.auth_release_orm import (
    RolePermissionsORM,
)

logger = logging.getLogger(__name__)
ANCHOR_RESOURCE = "_role_anchor"

_CACHE_TTL_ENV = "KB_PERMISSION_CACHE_TTL"
_DEFAULT_CACHE_TTL = 300.0

# ── The catalogue ────────────────────────────────────────────────────────────
CATALOG: dict[str, str] = {
    # permission name          resource (subsystem it acts on)
    "viewDocuments":           "documents",
    "editTableRow":            "documents",
    "addChunkVersion":         "documents",
    "addData":                 "data",
    "processLayer":            "data",
    "deleteData":              "data",
    "viewConflicts":           "conflicts",
    "editConflict":            "conflicts",
    "viewFleetStats":          "fleet",
    "viewKnowledgeBases":      "knowledge_bases",
    "retrieveKnowledge":       "retrieval",
    "viewGraph":               "graph",
    "runGraphQuery":           "graph",
    "viewPolicies":            "policies",
    "addFilteringPolicy":      "policies",
    "editFilteringPolicy":     "policies",
    "deleteFilteringPolicy":   "policies",
    "editExtractionPolicy":    "policies",
    "viewQdrantCollections":   "qdrant",
    "searchQdrant":            "qdrant",
    "toggleQdrant":            "qdrant",
    "viewTrash":               "trash",
    "viewWarehouses":          "warehouses",
    "addWarehouse":            "warehouses",
    "addWarehouseConfig":      "warehouses",
    "editWarehouseConfig":     "warehouses",
    "viewSources":             "sources",
    "addSource":               "sources",
    "editSource":              "sources",
    "deleteSource":            "sources",
    "runSource":               "sources",
}

PERMISSIONS: frozenset[str] = frozenset(CATALOG)


class UnknownPermission(ValueError):
    pass


class PermissionStoreUnavailable(RuntimeError):
    pass

def _cache_ttl() -> float:
    raw = os.environ.get(_CACHE_TTL_ENV, "")
    if not raw:
        return _DEFAULT_CACHE_TTL
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("%s=%r is not a number — using %ss", _CACHE_TTL_ENV, raw, _DEFAULT_CACHE_TTL)
        return _DEFAULT_CACHE_TTL


class PermissionStore:
    """A cached snapshot of the RolePermissions grant rows."""

    __slots__ = ("_grants", "_loaded_at", "_lock")

    def __init__(self) -> None:
        self._grants: dict[str, frozenset[str]] | None = None
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Force the next lookup to re-read Postgres."""
        self._loaded_at = 0.0

    async def _snapshot(self, postgres: Any) -> dict[str, frozenset[str]]:
        if self._grants is not None and (time.monotonic() - self._loaded_at) < _cache_ttl():
            return self._grants

        async with self._lock:
            # Another coroutine may have refreshed while we waited for the lock.
            if self._grants is not None and (time.monotonic() - self._loaded_at) < _cache_ttl():
                return self._grants
            try:
                await self._refresh(postgres)
            except Exception as e:
                if self._grants is None:
                    logger.error("permissions | cannot load the RBAC matrix from Postgres: %s", e)
                    raise PermissionStoreUnavailable(str(e)) from e
                # A stale matrix is a better failure than locking every user
                # out of a running service, so long as it says so.
                logger.warning(
                    "permissions | refresh failed (%s) — serving a snapshot that is %.0fs old",
                    e, time.monotonic() - self._loaded_at,
                )
            return self._grants or {}

    async def _refresh(self, postgres: Any) -> None:
        async with postgres.get_platform_client() as session:
            rows = (await session.execute(
                select(RolePermissionsORM.role_id, RolePermissionsORM.action)
                .where(RolePermissionsORM.resource != ANCHOR_RESOURCE)
            )).all()

        grants: dict[str, set[str]] = {}
        uncatalogued: set[str] = set()
        for role_id, action in rows:
            if action not in PERMISSIONS:
                uncatalogued.add(action)
                continue
            grants.setdefault(role_id, set()).add(action)

        if uncatalogued:
            logger.warning(
                "permissions | RolePermissions grants %d name(s) the catalogue does not "
                "define, ignoring them: %s — a row spelt this way grants nothing",
                len(uncatalogued), sorted(uncatalogued),
            )

        self._grants = {role: frozenset(names) for role, names in grants.items()}
        self._loaded_at = time.monotonic()
        logger.info(
            "permissions | loaded %d grant(s) across %d role(s) from a catalogue of %d",
            sum(len(v) for v in self._grants.values()), len(self._grants), len(PERMISSIONS),
        )

    async def granted_to(self, postgres: Any, role: str) -> frozenset[str]:
        """Every permission `role` holds. An unknown role holds none."""
        if not role:
            return frozenset()
        return (await self._snapshot(postgres)).get(role, frozenset())

    async def can(self, postgres: Any, role: str, *permission_names: str) -> bool:
        """True when `role` holds ANY of `permission_names`.

        `any`, not `all`, matching the registry this replaced: a route naming
        several permissions is asking "may this caller do it by any route".
        """
        if not role or not permission_names:
            return False
        granted = await self.granted_to(postgres, role)
        return any(name in granted for name in permission_names)


store = PermissionStore()
