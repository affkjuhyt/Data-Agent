"""Helpers for the V3 database tests: act as a service role, seed an organisation, expect a refusal."""

from __future__ import annotations

import uuid

import psycopg2


def as_role(
    conn, role: str, org_id: uuid.UUID | None, user_id: uuid.UUID | None = None, **gucs: str
) -> None:
    """Act as a service role in the current transaction, with the settings the app sets."""
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL ROLE {role}")
        if org_id is not None:
            cur.execute("SELECT set_config('app.org_id', %s, true)", (str(org_id),))
        if user_id is not None:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
        for name, value in gucs.items():
            cur.execute("SELECT set_config(%s, %s, true)", (f"app.{name}", value))


def as_admin(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("RESET ROLE")


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def new_org(conn) -> uuid.UUID:
    org = new_id()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO org.organization (id, code, name) VALUES (%s, %s, %s)",
            (str(org), f"org-{org.hex[:8]}", "Tổ chức thử"),
        )
    return org


def fails(conn, sql: str, params: tuple = (), *, code: str | None = None) -> str:
    """Run `sql` in a savepoint and assert it is refused; returns the error message."""
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT expect_failure")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            cur.execute("ROLLBACK TO SAVEPOINT expect_failure")
            if code:
                assert exc.pgcode == code, f"expected SQLSTATE {code}, got {exc.pgcode}: {exc}"
            return str(exc)
        cur.execute("ROLLBACK TO SAVEPOINT expect_failure")
    raise AssertionError(f"expected the statement to be refused: {sql}")


def one(conn, sql: str, params: tuple = ()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row and len(row) == 1 else row
