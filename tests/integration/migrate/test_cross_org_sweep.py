"""PL-09: no service role reaches another organisation's rows, in any table, in either database.

The feature tests check isolation table by table where someone thought of it; this sweep is
generated from the catalogue so a new table is covered the day it exists. For every table with an
`org_id` column, one row of organisation B is written as the admin (foreign keys and triggers
off: `session_replication_role = replica`; NOT NULL columns get a value of their type, a column
held to a list by a CHECK its first value). A table whose CHECKs still refuse that row has them
dropped and the row written again: the test's transaction is rolled back, and a CHECK says what a
row may hold, not who may see it. Then every `svc_*` role, acting for organisation A with the
settings the application sets, must:

* read none of B's rows (SELECT ... WHERE org_id = B, when it may read `org_id`);
* change none (UPDATE ... SET org_id = org_id WHERE org_id = B, when it may update);
* delete none (DELETE ... WHERE org_id = B, when it may delete).

A table whose row cannot be synthesised is not silently skipped: it must be listed in UNSEEDED
with the reason, and a listed table that seeds again fails the test so the list only shrinks.

Seeing 0 rows proves something only when the row can be seen at all, so each table also needs a
role that reads B's row when acting for B (the positive control). The tables where no role can,
because a restrictive policy narrows them to a person or to a visible answer, are named in
OWNER_SCOPED. For them, and for every table, the structural test holds the organisation layer
itself: row-level security on, the one org_isolation policy every table shares, and no other
permissive policy (permissive policies are OR-ed, so any other one could widen it) but the
relay's on the outboxes.

Tables without `org_id` are outside it: workspace.* (isolated by name and da_swap_version's owner
check) and the reference tables shared by every organisation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import pytest

from tests.integration.migrate._ddl import as_role, new_id

pytestmark = pytest.mark.integration

APP_SCHEMAS_SQL = (
    "n.nspname NOT IN ('pg_catalog','information_schema','public','pg_toast') "
    "AND n.nspname NOT LIKE 'pg_temp%'"
)
# Tables whose row of organisation B cannot be written generically, with why. Keep it short:
# each entry is a table the sweep does not cover.
UNSEEDED: dict[str, str] = {}
# No role reads B's row as B here: a restrictive policy on top of org_isolation narrows the table
# to its owner or successor (exec.*, the personal vault and handover, BR-189/190) or to the rows
# of an answer the reader may see (qa receipts). The structural test covers their org layer.
OWNER_SCOPED: dict[str, str] = {
    **{
        f"exec.{t}": "owner_only / workflow_only / successor_read restrict to one person"
        for t in (
            "handover",
            "handover_item",
            "key_access_log",
            "memory_item",
            "thesis",
            "thesis_evidence",
            "thesis_score",
            "vault",
            "vault_key_wrap",
        )
    },
    "qa.answer_receipt": "answer_receipt_answer_visible: only with an answer the reader sees",
    "qa.answer_receipt_figure": "answer_receipt_figure_receipt_visible: only with its receipt",
    "qa.receipt_replay": "receipt_replay_receipt_visible: only with its receipt",
}
ORG_ISOLATION = "(org_id = (NULLIF(current_setting('app.org_id'::text, true), ''::text))::uuid)"
RELAY_POLICIES = {
    "outbox_relay_read",
    "outbox_relay_publish",
    "outbox_relay_retry",
    "outbox_relay_dead_letter",
}


# The one cross-organisation reach the design wants (ADR-06, docs/RUNBOOK.md §10b): worker-agent's
# relay claims the events of every organisation from each schema's outbox (policy
# outbox_relay_read USING (true)); it may only mark them published, never change their org_id or
# delete them. Anything else that reaches B's rows is a leak.
def _intended(role: str, kind: str, table: str) -> bool:
    return role == "svc_outbox_relay" and kind == "read" and table.endswith(".outbox")


# Settings a policy may read besides app.org_id; random, so they never match B's row by chance.
GUCS = ("user_id", "data_space_id", "principal_id", "client_id")
_LIST = re.compile(r"ARRAY\[(.*?)\]")
_QUOTED = re.compile(r"'((?:[^']|'')*)'")


@dataclass
class Sweep:
    tables: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    unseeded: dict[str, str] = field(default_factory=dict)
    checks: int = 0
    leaks: list[str] = field(default_factory=list)
    intended: list[str] = field(default_factory=list)
    # Tables some role reads B's row of when acting for B: a probe of A there is not blind.
    seen_by_owner: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        # None, not (), without parameters: psycopg2 would read the % of a LIKE as a placeholder.
        cur.execute(sql, params or None)
        return cur.fetchall()


def _tables(conn: Any) -> list[str]:
    return [
        f"{s}.{t}"
        for s, t in _rows(
            conn,
            "SELECT n.nspname, c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
            f"WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND {APP_SCHEMAS_SQL} "
            "ORDER BY 1, 2",
        )
    ]


def _roles(conn: Any) -> list[str]:
    return [
        r
        for (r,) in _rows(
            conn, "SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\\_%' ORDER BY 1"
        )
    ]


def _listed_values(conn: Any, table: str) -> dict[str, str]:
    """A column a CHECK holds to a list of text values, with the first of them."""
    out: dict[str, str] = {}
    for (definition,) in _rows(
        conn,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c'",
        (table,),
    ):
        listed = _LIST.search(definition)
        if not listed or "ANY" not in definition:
            continue
        column = re.match(r"CHECK \(+\(?(\w+)", definition)
        values = _QUOTED.findall(listed.group(1))
        if column and values:
            out.setdefault(column.group(1), values[0].replace("''", "'"))
    return out


def _value(
    conn: Any, data_type: str, udt: str, column: str, listed: dict[str, str]
) -> tuple[str, Any]:
    """(SQL expression, parameter) for a NOT NULL column without a default."""
    if column in listed:
        return "%s", listed[column]
    if data_type == "uuid":
        return "%s", str(new_id())
    if data_type in ("text", "character varying", "character", "citext"):
        return "%s", "x"
    if data_type in ("smallint", "integer", "bigint", "numeric", "real", "double precision"):
        return "%s", 1
    if data_type == "boolean":
        return "%s", False
    if data_type in ("timestamp with time zone", "timestamp without time zone", "date"):
        return "now()", None
    if data_type in ("jsonb", "json"):
        return "%s::jsonb", "{}"
    if data_type == "bytea":
        return "%s", b"\x00"
    if data_type == "inet":
        return "%s::inet", "127.0.0.1"
    if data_type == "interval":
        return "%s::interval", "1 hour"
    if data_type == "ARRAY":
        return "'{}'", None
    if data_type == "USER-DEFINED":
        labels = _rows(
            conn,
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = %s ORDER BY e.enumsortorder LIMIT 1",
            (udt,),
        )
        if labels:
            return f"%s::{udt}", labels[0][0]
    raise LookupError(f"no value for {column} of type {data_type}/{udt}")


def _seed(conn: Any, table: str, org: uuid.UUID) -> None:
    schema, name = table.split(".")
    listed = _listed_values(conn, table)
    columns: list[str] = []
    exprs: list[str] = []
    params: list[Any] = []
    for column, data_type, udt, nullable, default, identity, generated in _rows(
        conn,
        "SELECT column_name, data_type, udt_name, is_nullable, column_default, is_identity, "
        "is_generated FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position",
        (schema, name),
    ):
        if generated == "ALWAYS" or identity == "YES":
            continue
        if column == "org_id":
            columns.append(column)
            exprs.append("%s")
            params.append(str(org))
            continue
        if nullable == "YES" or default is not None:
            continue
        expr, param = _value(conn, data_type, udt, column, listed)
        columns.append(f'"{column}"')
        exprs.append(expr)
        if "%s" in expr:
            params.append(param)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(exprs)})", params
        )


def _drop_checks(conn: Any, table: str) -> None:
    for (name,) in _rows(
        conn,
        "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c' "
        "AND coninhcount = 0",
        (table,),
    ):
        with conn.cursor() as cur:
            cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')


def _savepoint(conn: Any, action: Any) -> Exception | None:
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT sweep")
        try:
            action()
        except (psycopg2.Error, LookupError) as exc:
            cur.execute("ROLLBACK TO SAVEPOINT sweep")
            return exc
        cur.execute("RELEASE SAVEPOINT sweep")
    return None


def _may(conn: Any, role: str, table: str, privilege: str, column: str | None = None) -> bool:
    if column is None:
        sql, params = "SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)
    else:
        sql, params = (
            "SELECT has_column_privilege(%s, %s, %s, %s)",
            (role, table, column, privilege),
        )
    return bool(_rows(conn, sql, params)[0][0])


def _probe(conn: Any, role: str, a: uuid.UUID, b: uuid.UUID, table: str, sweep: Sweep) -> None:
    """Organisation A, as `role`, against B's row of `table`."""
    # An UPDATE or DELETE that names org_id in its WHERE needs to read it too.
    if not _may(conn, role, table, "SELECT", "org_id"):
        return
    probes = [("read", f"SELECT count(*) FROM {table} WHERE org_id = %s")]
    if _may(conn, role, table, "UPDATE", "org_id"):
        probes.append(("update", f"UPDATE {table} SET org_id = org_id WHERE org_id = %s"))
    if _may(conn, role, table, "DELETE"):
        probes.append(("delete", f"DELETE FROM {table} WHERE org_id = %s"))
    for kind, sql in probes:
        # Each probe in its own savepoint: an UPDATE or DELETE that leaked is undone, and a policy
        # that errors on a missing setting is reported, not mistaken for isolation.
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT probe")
        reached, error = 0, None
        try:
            as_role(conn, role, a, new_id(), **{g: str(new_id()) for g in GUCS if g != "user_id"})
            with conn.cursor() as cur:
                cur.execute(sql, (str(b),))
                reached = cur.fetchone()[0] if kind == "read" else cur.rowcount
        except psycopg2.Error as exc:
            error = exc
        with conn.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT probe")
            cur.execute("RESET ROLE")
        sweep.checks += 1
        if error is not None:
            sweep.errors.append(f"{role} {kind} {table}: {str(error).splitlines()[0]}")
        elif reached:
            who = f"{role} {kind} {table}"
            if _intended(role, kind, table):
                sweep.intended.append(who)
            else:
                sweep.leaks.append(f"{who}: {reached} row(s) of another organisation")


def _owner_reads(conn: Any, role: str, b: uuid.UUID, table: str) -> bool:
    """The positive control: `role` acting for B itself sees B's row."""
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT owner")
    try:
        as_role(conn, role, b, new_id(), **{g: str(new_id()) for g in GUCS if g != "user_id"})
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table} WHERE org_id = %s", (str(b),))
            return bool(cur.fetchone()[0])
    except psycopg2.Error:
        return False
    finally:
        with conn.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT owner")
            cur.execute("RESET ROLE")


def sweep(conn: Any) -> Sweep:
    s = Sweep(tables=_tables(conn))
    a, b = new_id(), new_id()
    with conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
    for table in s.tables:
        failed = _savepoint(conn, lambda t=table: _seed(conn, t, b))
        if failed is not None:
            failed = _savepoint(conn, lambda t=table: (_drop_checks(conn, t), _seed(conn, t, b)))
        if failed is None:
            s.seeded.append(table)
        else:
            s.unseeded[table] = str(failed).splitlines()[0]
    for role in _roles(conn):
        for table in s.seeded:
            _probe(conn, role, a, b, table, s)
            if (
                table not in s.seen_by_owner
                and _may(conn, role, table, "SELECT", "org_id")
                and _owner_reads(conn, role, b, table)
            ):
                s.seen_by_owner.add(table)
    return s


def _check(s: Sweep, unseeded_known: dict[str, str]) -> None:
    blind = set(s.seeded) - s.seen_by_owner
    assert blind == set(OWNER_SCOPED) & set(s.seeded), (
        "tables no role reads as their own organisation (a probe there proves nothing): "
        f"unexpected {sorted(blind - set(OWNER_SCOPED))}, "
        f"readable now {sorted(set(OWNER_SCOPED) & set(s.seeded) - blind)}"
    )
    print(
        f"\ncross-org sweep: {len(s.tables)} tables with org_id, {len(s.seeded)} seeded, "
        f"{s.checks} role x table checks, {len(s.leaks)} leaks, {len(s.errors)} errors, "
        f"{len(s.seen_by_owner)} tables read by their own organisation"
    )
    assert not s.leaks, "rows of another organisation reached:\n" + "\n".join(s.leaks)
    # The relay does reach every seeded outbox: a sweep that stopped seeing it would be blind.
    outboxes = sorted(t for t in s.seeded if t.endswith(".outbox"))
    assert sorted(s.intended) == [f"svc_outbox_relay read {t}" for t in outboxes]
    assert not s.errors, "probes that errored (a policy or grant to look at):\n" + "\n".join(
        s.errors
    )
    new = {t: why for t, why in s.unseeded.items() if t not in unseeded_known}
    assert not new, (
        "tables the sweep could not seed (fix the seeding or list them in UNSEEDED):\n"
        + "\n".join(f"{t}: {why}" for t, why in new.items())
    )
    stale = [t for t in unseeded_known if t in s.seeded]
    assert not stale, f"listed in UNSEEDED but seeded now; remove them: {stale}"


def test_no_role_reaches_another_organisation_in_dataagent(db: Any) -> None:
    _check(sweep(db), UNSEEDED)


def test_no_role_reaches_another_organisation_in_dataagent_audit(audit_db: Any) -> None:
    _check(sweep(audit_db), UNSEEDED)


def test_the_sweep_sees_a_policy_that_leaks(db: Any) -> None:
    """The sweep is not blind: a policy that lets svc_qa read every organisation's questions is
    reported (the transaction is rolled back, so the policy never outlives the test)."""
    with db.cursor() as cur:
        cur.execute("CREATE POLICY sweep_leak ON qa.question FOR SELECT TO svc_qa USING (true)")
    s = sweep(db)
    assert any(leak.startswith("svc_qa read qa.question:") for leak in s.leaks), s.leaks


@pytest.mark.parametrize("database", ["db", "audit_db"])
def test_every_org_table_has_the_one_organisation_policy(
    request: pytest.FixtureRequest, database: str
) -> None:
    conn = request.getfixturevalue(database)
    tables = _tables(conn)
    rows = _rows(
        conn,
        "SELECT n.nspname || '.' || c.relname, c.relrowsecurity, p.permissive, "
        "array_to_string(p.roles, ','), p.qual "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_policies p ON p.schemaname = n.nspname AND p.tablename = c.relname "
        "AND p.policyname = 'org_isolation' "
        "WHERE n.nspname || '.' || c.relname = ANY(%s)",
        (tables,),
    )
    wrong = [r[0] for r in rows if r[1:] != (True, "PERMISSIVE", "public", ORG_ISOLATION)]
    assert len(rows) == len(tables) and not wrong, f"without the organisation policy: {wrong}"
    others = _rows(
        conn,
        "SELECT p.schemaname || '.' || p.tablename, p.policyname, array_to_string(p.roles, ',') "
        "FROM pg_policies p WHERE p.permissive = 'PERMISSIVE' AND p.policyname <> 'org_isolation' "
        "AND p.schemaname || '.' || p.tablename = ANY(%s)",
        (tables,),
    )
    widening = [
        o
        for o in others
        if not (o[0].endswith(".outbox") and o[1] in RELAY_POLICIES and o[2] == "svc_outbox_relay")
    ]
    assert not widening, f"permissive policies that could widen org_isolation: {widening}"
