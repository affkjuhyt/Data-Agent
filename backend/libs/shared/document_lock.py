"""Advisory lock scoping one document, shared by ingestion and promotion.

Keyed on (tenant_id, name) so a re-upload cannot destroy a document while a
promotion is still writing to it.
"""

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)


class DocumentBusy(Exception):
    """Another stage already holds this document's lock."""

    def __init__(self, tenant_id: str, name: str) -> None:
        super().__init__(f"document '{name}' is locked for tenant {tenant_id}")
        self.tenant_id = tenant_id
        self.name = name


def document_lock_key(tenant_id: str, name: str) -> int:
    digest = hashlib.blake2b(f"{tenant_id}:{name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@asynccontextmanager
async def document_lock(postgres: Any, tenant_id: str, name: str) -> AsyncIterator[None]:
    """Hold the document lock for the block, or raise DocumentBusy immediately.

    Never queues: a waiting caller would pin its connection for the whole stage.
    The session stays in one transaction so the lock keeps the same backend
    under transaction pooling.
    """
    key = document_lock_key(tenant_id, name)
    # pg_advisory_lock is a session-level primitive, not a read of any
    # tenant-scoped table (the lock key already encodes tenant_id via the
    # hash) — get_platform_client() is the deliberately-unscoped session type;
    # get_client() (bare, no tenant_id arg) instead falls back to the ambient
    # ContextVar (see postgres_connector.get_client's docstring), and once a
    # caller sets that, it returns an unentered async-context-manager object
    # here rather than the plain AsyncSession this code calls .scalar()/
    # .close() on directly — dormant until something started setting that
    # ContextVar, which nothing did until the promote_silver worker handler fix.
    session = postgres.get_platform_client()
    acquired = await session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    if not acquired:
        await session.close()
        raise DocumentBusy(tenant_id, name)
    try:
        yield
    finally:
        try:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        except Exception:
            log.exception("failed to release document lock tenant=%s name=%s", tenant_id, name)
        await session.close()
