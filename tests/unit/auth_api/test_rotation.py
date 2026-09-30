"""services/auth_api/services/rotation.py: what the scheduled sweep records.

A rotation Vault refused must not be booked as done: the old code bumped the version
and pushed rotation_due_at out 90 days regardless, so a failed rotation silently
skipped the key for a quarter. The database is a scripted fake connection.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROW = {"id": "s-1", "tenant_id": "t-1", "key_name": "k", "key_type": "ENCRYPTION_KEY", "version": 3}


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.statements.append((" ".join(sql.split()), params))
        self._sql = sql

    def fetchall(self) -> list[dict[str, Any]]:
        return [ROW]

    def fetchone(self) -> Any:
        if "FOR UPDATE SKIP LOCKED" in self._sql:
            return {"id": "s-1"} if self.conn.claim else None
        return {"value": True}


class FakeConn:
    def __init__(self, claim: bool = True) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.claim = claim
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_: Any) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


@pytest.fixture
def sweep(monkeypatch: pytest.MonkeyPatch):
    from services.auth_api.services import rotation

    conns: list[FakeConn] = []

    def _get_db() -> FakeConn:
        # first connection reads the overdue list, later ones are per-key transactions
        conn = FakeConn(claim=state["claim"])
        conns.append(conn)
        return conn

    state = {"claim": True, "vault_fails": False}

    def _rotate_key(**_: Any) -> None:
        if state["vault_fails"]:
            raise RuntimeError("vault sealed")

    vault = SimpleNamespace(secrets=SimpleNamespace(transit=SimpleNamespace(rotate_key=_rotate_key)))
    monkeypatch.setattr(rotation, "get_db", _get_db)
    monkeypatch.setattr(rotation, "get_vault", lambda: vault)
    monkeypatch.setattr(rotation, "get_governance_setting", lambda cur, name: True)
    return rotation, conns, state


def _sql(conn: FakeConn) -> list[str]:
    return [s for s, _ in conn.statements]


def test_a_successful_rotation_advances_the_version_and_the_due_date(sweep: Any) -> None:
    rotation, conns, _ = sweep
    rotation.run_auto_rotation()
    txn = conns[1]
    assert any(s.startswith("UPDATE secrets_vault SET version") for s in _sql(txn))
    inserted = [p for s, p in txn.statements if s.startswith("INSERT INTO key_rotations")][0]
    assert inserted[1:5] == (3, 4, "SUCCESS", None)
    assert txn.commits == 1


def test_a_failed_rotation_is_recorded_but_the_key_stays_overdue(sweep: Any) -> None:
    rotation, conns, state = sweep
    state["vault_fails"] = True
    rotation.run_auto_rotation()
    txn = conns[1]
    assert not any(s.startswith("UPDATE secrets_vault") for s in _sql(txn))
    inserted = [p for s, p in txn.statements if s.startswith("INSERT INTO key_rotations")][0]
    assert inserted[1:5] == (3, 3, "FAILED", "vault sealed")


def test_a_key_another_sweep_already_claimed_is_skipped(sweep: Any) -> None:
    rotation, conns, state = sweep
    state["claim"] = False
    rotation.run_auto_rotation()
    txn = conns[1]
    assert not any(s.startswith(("UPDATE secrets_vault", "INSERT INTO key_rotations")) for s in _sql(txn))
    assert txn.commits == 0
