"""Connection tests requested by the catalog API.

The API deliberately records ``source.test_requested`` and returns 202.  This handler is the
small connector-worker boundary for the first V3 connector: PostgreSQL.  It never stores source
credentials; OpenBao is read through the same SecretReader used by in-place queries.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import uuid

import asyncpg
import httpx
from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.secrets import SecretUnavailable
from services.worker_agent.questions import secrets

logger = logging.getLogger(__name__)

EVENT_TYPE = "source.test_requested"
ROLE = "svc_catalog"
TIMEOUT_SECONDS = 30


def _ssl_mode(source: catalog.Source) -> str:
    options = source.conn_options or {}
    # The local demo PostgreSQL service has no CA bundle. Production sources keep the secure
    # default; an explicit sslmode in conn_options always wins.
    default = "disable" if (source.host or "").endswith(".svc.cluster.local") else "verify-full"
    mode = str(options.get("sslmode") or default)
    if mode not in {"disable", "require", "verify-ca", "verify-full"}:
        raise SecretUnavailable(f"sslmode {mode} is not supported")
    return mode


def _clickhouse_url(host: str, port: int) -> str:
    # The playground is HTTPS on 443/8443. A slash or userinfo in the host would retarget the
    # request, so those are refused before the URL is built.
    if not host or any(char in host for char in "/?#@"):
        raise ValueError("host")
    scheme = "https" if port in (443, 8443) else "http"
    return f"{scheme}://{host}:{port}/"


async def _clickhouse_sql(
    client: httpx.AsyncClient, url: str, database: str | None, sql: str, auth: tuple[str, str]
) -> httpx.Response:
    params = {"database": database} if database else None
    return await client.post(url, params=params, content=sql.encode(), auth=auth)


async def _check_clickhouse(
    source: catalog.Source, org_id: uuid.UUID
) -> tuple[bool, str, str | None]:
    """HTTP ping, a read, and a refused write. The reason never includes the driver text."""
    try:
        credentials = await secrets().read(org_id, source.secret_ref)
        url = _clickhouse_url(source.host or "", source.port or 8123)
    except (SecretUnavailable, ValueError) as exc:
        return False, f"connection failed ({type(exc).__name__})", None
    auth = (credentials.user, credentials.password)
    database = source.database_name or None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, verify=True) as client:
            version_response = await _clickhouse_sql(
                client, url, database, "SELECT version()", auth
            )
            if version_response.status_code >= 400:
                return False, "connection failed (HTTPStatusError)", None
            version = version_response.text.strip()[:80] or None
            # Drop the probe if the account can create it: the check must not leave a database.
            created = await _clickhouse_sql(
                client, url, database, "CREATE DATABASE da_connector_probe", auth
            )
            if created.status_code < 400:
                with contextlib.suppress(httpx.HTTPError, OSError, TimeoutError):
                    await _clickhouse_sql(
                        client, url, database, "DROP DATABASE IF EXISTS da_connector_probe", auth
                    )
                return False, "the source account has write privileges", version
            read = await _clickhouse_sql(client, url, database, "SELECT 1", auth)
            if read.status_code >= 400:
                return False, "read-only check failed (HTTPStatusError)", None
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        return False, f"connection failed ({type(exc).__name__})", None
    return True, "connection, login, catalog and read-only checks passed", version


async def _check(source: catalog.Source, org_id: uuid.UUID) -> tuple[bool, str, str | None]:
    """Open the source, run read-only capability checks, and return a safe failure reason."""
    if source.kind == "clickhouse":
        return await _check_clickhouse(source, org_id)
    if source.kind != "postgresql":
        return False, f"connector {source.kind} is not wired yet", None
    try:
        credentials = await secrets().read(org_id, source.secret_ref)
        conn = await asyncpg.connect(
            host=source.host,
            port=source.port or 5432,
            database=source.database_name,
            user=credentials.user,
            password=credentials.password,
            ssl=_ssl_mode(source),
            timeout=TIMEOUT_SECONDS,
            statement_cache_size=0,
            server_settings={"application_name": "dataagent-connector-test"},
        )
    except (
        SecretUnavailable,
        asyncpg.ClientConfigurationError,
        asyncpg.PostgresError,
        OSError,
        TimeoutError,
    ) as exc:
        # Do not persist driver messages: they can contain usernames, host details or SQL.
        return False, f"connection failed ({type(exc).__name__})", None

    try:
        async with conn.transaction(readonly=True):
            version = str(await conn.fetchval("SELECT current_setting('server_version')"))
            can_create_db = bool(
                await conn.fetchval(
                    "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
                )
            )
            can_create_schema = bool(
                await conn.fetchval("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            )
            await conn.fetchval("SELECT 1 FROM information_schema.tables LIMIT 1")
        if can_create_db or can_create_schema:
            return False, "the source account has write privileges", version
        return True, "connection, login, catalog and read-only checks passed", version
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        return False, f"read-only check failed ({type(exc).__name__})", None
    finally:
        await conn.close()


async def test_source(event: Event) -> None:
    source_id = uuid.UUID(str(event.payload["source_id"]))
    async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
        source = await session.scalar(
            select(catalog.Source)
            .where(catalog.Source.id == source_id, catalog.Source.org_id == event.org_id)
            .with_for_update()
        )
        if source is None or source.status != "testing":
            return

    passed, reason, version = await _check(source, event.org_id)
    async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
        source = await session.scalar(
            select(catalog.Source)
            .where(catalog.Source.id == source_id, catalog.Source.org_id == event.org_id)
            .with_for_update()
        )
        if source is None or source.status != "testing":
            return
        source.status = "active" if passed else "needs_fix"
        source.status_reason = reason
        source.db_version = version
        source.last_checked_at = datetime.datetime.now(datetime.UTC)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: test_source}
