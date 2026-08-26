"""Redis exact-key result cache for KB retrieval (KB-SCALE-10K.md lever #2).

An L0 cache that sits BEFORE embedding: an exact repeat of a query (the bulk of
search-box traffic) returns instantly without touching the model, Qdrant, Neo4j,
or Postgres. Complements the in-pipeline Qdrant *semantic* cache (which catches
paraphrases but must embed first).

Design:
* **Tenant-scoped, versioned keys.** Result key embeds a per-tenant generation
  number; invalidation is a single ``INCR`` of that tenant's version → every
  cached result for the tenant is orphaned at once (O(1), no SCAN), and the
  orphans expire on their own TTL. Correct for "a new document changed what this
  tenant can find".
* **Native TTL** on every entry (a safety net even without an invalidation).
* **Best-effort.** Redis being down never breaks retrieval — every op swallows
  errors and behaves as a miss / no-op.

The client is injected as a ``RedisLike`` protocol so this module stays free of
any concrete connector dependency.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional, Protocol

log = logging.getLogger(__name__)

_PREFIX = "kbrc"


class RedisLike(Protocol):
    async def get(self, key: str) -> Optional[str]: ...
    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None: ...
    async def incr(self, key: str) -> int: ...


def query_hash(
    query: str,
    collection: str,
    entities: Optional[list[str]] = None,
    intent: Optional[str] = None,
    entity_types: Optional[dict[str, str]] = None,
) -> str:
    """Stable hash of everything that changes the result set. Whitespace-folded +
    lowercased query and sorted collections so trivially-different requests share
    a key."""
    et = entity_types or {}
    payload = json.dumps(
        {
            "q": " ".join((query or "").split()).lower(),
            "c": collection or "",
            "e": sorted(entities or []),
            "i": (intent or "").strip().lower(),
            "t": {k: et[k] for k in sorted(et)},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _version_key(tenant_id: str) -> str:
    return f"{_PREFIX}:ver:{tenant_id}"


class ResultCache:
    def __init__(self, redis: RedisLike, ttl_seconds: int, enabled: bool = True) -> None:
        self._r = redis
        self._ttl = ttl_seconds
        self._enabled = enabled

    async def _version(self, tenant_id: str) -> str:
        try:
            v = await self._r.get(_version_key(tenant_id))
            return v or "0"
        except Exception:  # noqa: BLE001 — best-effort
            return "0"

    def _result_key(self, tenant_id: str, version: str, kb: Optional[str], qhash: str) -> str:
        return f"{_PREFIX}:{tenant_id}:v{version}:{kb or '_all'}:{qhash}"

    async def get(self, tenant_id: str, kb: Optional[str], qhash: str) -> Optional[dict[str, Any]]:
        if not self._enabled:
            return None
        try:
            version = await self._version(tenant_id)
            raw = await self._r.get(self._result_key(tenant_id, version, kb, qhash))
            if not raw:
                return None
            return json.loads(raw)  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001 — best-effort → treat as miss
            return None

    async def set(self, tenant_id: str, kb: Optional[str], qhash: str, value: dict[str, Any]) -> None:
        if not self._enabled:
            return
        try:
            version = await self._version(tenant_id)
            key = self._result_key(tenant_id, version, kb, qhash)
            await self._r.set(key, json.dumps(value, ensure_ascii=False), self._ttl)
        except Exception as e:  # noqa: BLE001 — best-effort → skip caching
            log.debug("result-cache set skipped: %s", e)

    async def invalidate(self, tenant_id: str) -> None:
        """Bump the tenant generation → all cached results for the tenant orphaned
        (TTL reclaims them). Call after searchable content changes (gold promote)."""
        try:
            await self._r.incr(_version_key(tenant_id))
        except Exception as e:  # noqa: BLE001 — advisory, never fatal
            log.warning("result-cache invalidate failed for tenant %s: %s", tenant_id, e)
