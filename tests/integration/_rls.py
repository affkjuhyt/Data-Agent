"""Row-level security test utilities shared by the integration suites (NOP-148).

Every V2 table story (NOP-90, 95, 104, 106, 109, 117, 120, 124, 126, 131, 137…)
proves its own isolation with these, so the checks read the same everywhere:

    with as_service(dsn, "ingest_svc", TENANT_A) as cur:
        cur.execute("SELECT count(*) FROM ingest.release")

`rls_violations` is the CI guard: it lists every V2 table that is not under the
policy of `libs/shared/tenant_rls.py`.

Privileges come from SET ROLE on the superuser connection the suites already
hold: after SET ROLE, Postgres checks the target role's privileges and applies
its row-level security, which is what that service's own login gets.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2

from libs.shared.sql_identifiers import quote_identifier, safe_identifier
from libs.shared.tenant_rls import V2_SCHEMAS


@contextmanager
def as_service(dsn: str, role: str, tenant_id: str | None = None) -> Iterator[Any]:
    """A cursor acting as `role`, working for `tenant_id` (None: no tenant set,
    the state a connection is in before a request sets one). Everything done
    through it is rolled back, so tests can share fixture rows."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET ROLE {quote_identifier(safe_identifier(role, 'role'))}")
            if tenant_id is not None:
                # is_local=true: the transaction-scoped form a pooled connection uses.
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            yield cur
    finally:
        conn.rollback()
        conn.close()


def rls_violations(
    dsn: str,
    *,
    schemas: Iterable[str] = V2_SCHEMAS,
    shared_tables: Iterable[str] = (),
    tenant_column: str = "tenant_id",
) -> list[str]:
    """One line per table in `schemas` that breaks ADR-V2-28. A table must have a
    NOT NULL tenant column, ENABLE and FORCE row level security and a SELECT
    policy, unless it is listed in `shared_tables` ("schema.table"): the few
    tables that are deliberately not per tenant (unit catalogue, trading calendar)
    and are named as such where the guard runs."""
    shared = set(shared_tables)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       a.attnotnull,
                       EXISTS (SELECT 1 FROM pg_policy p
                               WHERE p.polrelid = c.oid AND p.polcmd IN ('r', '*'))
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_attribute a
                       ON a.attrelid = c.oid AND a.attname = %s AND NOT a.attisdropped
                WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
                ORDER BY 1, 2
                """,
                (tenant_column, list(schemas)),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    problems = []
    for schema, table, enabled, forced, not_null, has_select_policy in rows:
        name = f"{schema}.{table}"
        if name in shared:
            continue
        missing = []
        if not_null is None:
            missing.append(f"no {tenant_column} column")
        elif not not_null:
            missing.append(f"{tenant_column} is nullable")
        if not enabled:
            missing.append("RLS not enabled")
        if not forced:
            missing.append("RLS not forced")
        if not has_select_policy:
            missing.append("no SELECT policy")
        if missing:
            problems.append(f"{name}: {', '.join(missing)}")
    return problems
