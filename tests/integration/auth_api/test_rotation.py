"""services/auth_api/services/rotation.py: what the scheduled sweep records.

A rotation Vault refused must not be booked as done: the old code bumped the
version and pushed rotation_due_at out 90 days regardless, so a failed rotation
silently skipped the key for a quarter. Two sweeps (two pods) must not rotate the
same key twice: the per-key transaction claims the row FOR UPDATE SKIP LOCKED, so
a key another sweep holds is left alone. Runs against real Postgres; OpenBao is a
stand-in exposing only transit.rotate_key.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from .conftest import TENANT_1

pytestmark = pytest.mark.integration


class _Transit:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.rotated: list[str] = []

    def rotate_key(self, *, name: str, mount_point: str) -> None:
        if self.error is not None:
            raise self.error
        self.rotated.append(name)


@pytest.fixture
def sweep(auth_api_app, migrated_auth_db, db_conn, monkeypatch: pytest.MonkeyPatch):
    from services.auth_api.services import rotation

    transit = _Transit()
    vault = SimpleNamespace(secrets=SimpleNamespace(transit=transit))
    monkeypatch.setattr(rotation, "get_vault", lambda: vault)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE platform_settings SET value = 'true' WHERE key = 'vault_auto_rotation'")
    return rotation, transit


def _seed_overdue(
    db_conn, *, key_type: str = "ENCRYPTION_KEY", version: int = 3
) -> tuple[str, datetime]:
    due = datetime.now(UTC) - timedelta(days=1)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO secrets_vault (tenant_id, key_name, key_type, version, rotation_due_at) "
            "VALUES (%s, 'k', %s, %s, %s) RETURNING id",
            (TENANT_1, key_type, version, due),
        )
        return str(cur.fetchone()["id"]), due


def _secret(db_conn, secret_id: str) -> dict[str, Any]:
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM secrets_vault WHERE id = %s", (secret_id,))
        return cur.fetchone()


def _rotations(db_conn) -> list[tuple]:
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT secret_id::text, triggered_by, actor_id, old_version, new_version, status, error "
            "FROM key_rotations"
        )
        return [tuple(r.values()) for r in cur.fetchall()]


def test_a_successful_rotation_advances_the_version_and_the_due_date(sweep, db_conn) -> None:
    rotation, transit = sweep
    secret_id, due = _seed_overdue(db_conn)

    rotation.run_auto_rotation()

    assert len(transit.rotated) == 1
    row = _secret(db_conn, secret_id)
    assert row["version"] == 4
    assert row["rotation_due_at"] > datetime.now(UTC) + timedelta(days=89)
    assert row["last_rotated_at"] is not None
    assert _rotations(db_conn) == [(secret_id, "SCHEDULED", "system", 3, 4, "SUCCESS", None)]


def test_a_failed_rotation_is_recorded_but_the_key_stays_overdue(sweep, db_conn) -> None:
    rotation, transit = sweep
    transit.error = RuntimeError("vault sealed")
    secret_id, due = _seed_overdue(db_conn)

    rotation.run_auto_rotation()

    row = _secret(db_conn, secret_id)
    assert (row["version"], row["rotation_due_at"], row["last_rotated_at"]) == (3, due, None)
    assert _rotations(db_conn) == [
        (secret_id, "SCHEDULED", "system", 3, 3, "FAILED", "vault sealed")
    ]


def test_a_key_another_sweep_already_claimed_is_skipped(sweep, migrated_auth_db, db_conn) -> None:
    rotation, transit = sweep
    secret_id, due = _seed_overdue(db_conn)
    other_sweep = psycopg2.connect(migrated_auth_db["dsn"])
    try:
        with other_sweep.cursor() as cur:
            cur.execute("SELECT id FROM secrets_vault WHERE id = %s FOR UPDATE", (secret_id,))
            # In a thread so a sweep that waits for the lock (no SKIP LOCKED) fails
            # the test instead of hanging it.
            this_sweep = threading.Thread(target=rotation.run_auto_rotation)
            this_sweep.start()
            this_sweep.join(timeout=10)
            assert not this_sweep.is_alive(), "the sweep waited for a key another sweep holds"
    finally:
        other_sweep.rollback()
        other_sweep.close()
    this_sweep.join(timeout=10)

    assert transit.rotated == []
    assert _secret(db_conn, secret_id)["version"] == 3
    assert _rotations(db_conn) == []


def test_nothing_rotates_while_auto_rotation_is_off(sweep, db_conn) -> None:
    rotation, transit = sweep
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE platform_settings SET value = 'false' WHERE key = 'vault_auto_rotation'"
        )
    secret_id, _due = _seed_overdue(db_conn)

    rotation.run_auto_rotation()

    assert transit.rotated == []
    assert _rotations(db_conn) == []
