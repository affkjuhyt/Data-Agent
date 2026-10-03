"""Row-level security for one V2 table, as the SQL a migration runs (NOP-148).

Why this exists: every V2 table carries `tenant_id` and must be isolated by
Postgres itself, not by each query remembering its `WHERE` (docs/v2/10
ADR-V2-28). The v1 migrations each pasted their own `tenant_isolation` policy;
V2 has a dozen table stories (NOP-90, 95, 104, 106, 109, 117, 120, 124, 126,
131, 137…) in different hands, so the policy lives here once and each of them
calls it in the migration that creates its table:

    for sql in enable_tenant_rls("ingest", "release"):
        op.execute(sql)

One statement per item because asyncpg (the migrate Job's driver) rejects
several statements in one prepared call.

What a table gets:
  * ENABLE and FORCE row level security. FORCE applies the policies to the table
    owner too; without it the role that ran the migration reads every tenant.
  * One permissive policy per command, all on the tenant expression below:
    SELECT (USING), INSERT (WITH CHECK), UPDATE (USING and WITH CHECK, so a row
    can neither be edited across tenants nor moved to another tenant). DELETE
    gets a policy only with `allow_delete=True`: most V2 tables are append-only
    (ADR-V2-30), and under FORCE RLS a command with no policy matches no row.
  * No tenant set means NULL, which equals nothing: 0 rows, every insert refused.
  * `REVOKE ALL ... FROM PUBLIC`. The owning service role keeps the privileges
    it got from its schema's default privileges (NOP-147, 5b7d9f1a3c6e); reads
    from another schema are granted per table by the table's own migration.
  * `cross_tenant_roles`: roles that legitimately work across tenants (the
    outbox relay, docs/v2/14 §9.1; the maintenance role, ADR-V2-33) get their
    own SELECT/UPDATE (and DELETE with `allow_delete`) policy with `USING (true)`
    scoped `TO` that role only. Never INSERT: a cross-tenant role does not create
    tenant rows. Privileges still decide which commands the role may run.

`tests/integration/_rls.py` holds the matching test utilities, and
`tests/integration/workflow_runtime/test_v2_tenant_rls.py` fails CI for any V2
table that skipped this.

Pure: builds strings, touches no database (.importlinter keeps `libs.shared` a
leaf). Every name is validated as a plain identifier and quoted, so nothing a
caller passes can change the statement.
"""

from __future__ import annotations

from collections.abc import Iterable

from libs.shared.sql_identifiers import quote_identifier, safe_identifier

# The tenant the connection works for, as every v1 policy already reads it.
# `true` makes a missing setting NULL instead of an error; NULLIF turns the ''
# a pooled connection is reset to back into NULL.
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

V2_SCHEMAS: tuple[str, ...] = ("registry", "ingest", "governance", "knowledge", "alert")

_TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")


def _table(schema: str, table: str) -> str:
    return f"{quote_identifier(safe_identifier(schema, 'schema'))}.{quote_identifier(safe_identifier(table, 'table'))}"


def _cross_tenant_policies(role: str, allow_delete: bool) -> list[tuple[str, str]]:
    commands = ["select", "update", *(["delete"] if allow_delete else [])]
    return [(f"cross_tenant_{cmd}_{role}", cmd) for cmd in commands]


def enable_tenant_rls(
    schema: str,
    table: str,
    *,
    tenant_column: str = "tenant_id",
    allow_delete: bool = False,
    cross_tenant_roles: Iterable[str] = (),
) -> list[str]:
    """Statements that put `schema.table` under tenant isolation. Idempotent:
    every policy is dropped before it is created, so a re-run converges."""
    target = _table(schema, table)
    col = quote_identifier(safe_identifier(tenant_column, "tenant column"))
    roles = [safe_identifier(r, "role") for r in cross_tenant_roles]
    same_tenant = f"{col} = {TENANT_EXPR}"

    sql = [
        f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {target} FORCE ROW LEVEL SECURITY",
        f"REVOKE ALL ON TABLE {target} FROM PUBLIC",
    ]
    policies = [
        ("tenant_select", f"FOR SELECT USING ({same_tenant})"),
        ("tenant_insert", f"FOR INSERT WITH CHECK ({same_tenant})"),
        ("tenant_update", f"FOR UPDATE USING ({same_tenant}) WITH CHECK ({same_tenant})"),
    ]
    if allow_delete:
        policies.append(("tenant_delete", f"FOR DELETE USING ({same_tenant})"))
    for name, body in policies:
        sql.append(f"DROP POLICY IF EXISTS {name} ON {target}")
        sql.append(f"CREATE POLICY {name} ON {target} AS PERMISSIVE {body}")

    for role in roles:
        for name, cmd in _cross_tenant_policies(role, allow_delete):
            check = " WITH CHECK (true)" if cmd == "update" else ""
            sql.append(f"DROP POLICY IF EXISTS {quote_identifier(name)} ON {target}")
            sql.append(
                f"CREATE POLICY {quote_identifier(name)} ON {target} AS PERMISSIVE "
                f"FOR {cmd.upper()} TO {quote_identifier(role)} USING (true){check}"
            )
    return sql


def disable_tenant_rls(
    schema: str, table: str, *, cross_tenant_roles: Iterable[str] = ()
) -> list[str]:
    """The downgrade of `enable_tenant_rls`. A table migration that drops its
    table does not need this; it is for one that keeps the table."""
    target = _table(schema, table)
    names = list(_TENANT_POLICIES)
    for role in cross_tenant_roles:
        names += [
            name
            for name, _ in _cross_tenant_policies(safe_identifier(role, "role"), allow_delete=True)
        ]
    return [
        *(f"DROP POLICY IF EXISTS {quote_identifier(n)} ON {target}" for n in names),
        f"ALTER TABLE {target} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {target} DISABLE ROW LEVEL SECURITY",
    ]
