"""routers/compliance.py against real Postgres.

`pii_masking` stored here is read by the KB worker at ingestion to decide
whether PII is redacted, so the settings write is an enforcement switch: these
tests pin that it lands under the caller's tenant (and only there), that input
is normalised before it is stored, that a rejected write stores nothing, and
that the score is computed from what is actually stored. The DB-free checks
(auth guard, role gate, body validation, PII scan) are in
tests/unit/auth_api/test_compliance_router.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import TENANT_1, TENANT_2

pytestmark = pytest.mark.integration


def _seed(db_conn, tenant: str, **values: Any) -> None:
    columns = {"tenant_id": tenant, **values}
    with db_conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO compliance_settings ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            tuple(columns.values()),
        )


def _stored(db_conn, tenant: str) -> dict[str, Any] | None:
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM compliance_settings WHERE tenant_id = %s", (tenant,))
        return cur.fetchone()


class TestSettings:
    def test_get_without_stored_row_returns_defaults_for_header_tenant(
        self,
        migrated_client,
        headers_factory,
        db_conn,
    ) -> None:
        # Another tenant's stored settings must not leak into this one's.
        _seed(db_conn, TENANT_2, pii_masking=True, retention_days=7)

        resp = migrated_client.get(
            "/api/auth/compliance/settings", headers=headers_factory(tenant=TENANT_1)
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["pii_masking"] is False
        assert body["retention_days"] == 365
        assert body["updated_at"] is None

    def test_put_normalises_types_and_upserts_under_header_tenant(
        self,
        migrated_client,
        headers_factory,
        db_conn,
    ) -> None:
        _seed(db_conn, TENANT_2, pii_masking=False, retention_days=7)

        resp = migrated_client.put(
            "/api/auth/compliance/settings",
            json={
                "pii_masking": True,
                "pii_types": [" email ", "not-a-type"],
                "retention_days": 30,
            },
            headers=headers_factory(user_id="user-1", tenant=TENANT_1),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["pii_types"] == ["EMAIL"]
        row = _stored(db_conn, TENANT_1)
        assert row is not None, "the upsert was not committed under the header tenant"
        # Unknown PII types are dropped and the rest upper-cased so the worker
        # only ever sees names it can match.
        assert (row["pii_masking"], row["pii_types"], row["retention_days"], row["updated_by"]) == (
            True,
            "EMAIL",
            30,
            "user-1",
        )
        # Fields the body left out keep their current (default) values.
        assert row["residency_region"] == "Asia-SE1"
        assert _stored(db_conn, TENANT_2)["retention_days"] == 7

    def test_put_over_a_stored_row_updates_it_and_returns_the_new_values(
        self,
        migrated_client,
        headers_factory,
        db_conn,
    ) -> None:
        _seed(db_conn, TENANT_1, pii_masking=False, retention_days=90, residency_region="EU-W1")

        resp = migrated_client.put(
            "/api/auth/compliance/settings",
            json={"pii_masking": True},
            headers=headers_factory(),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["pii_masking"] is True
        row = _stored(db_conn, TENANT_1)
        assert (row["pii_masking"], row["retention_days"], row["residency_region"]) == (
            True,
            90,
            "EU-W1",
        )

    def test_put_negative_retention_is_422_and_not_written(
        self,
        migrated_client,
        headers_factory,
        db_conn,
    ) -> None:
        resp = migrated_client.put(
            "/api/auth/compliance/settings",
            json={"retention_days": -1},
            headers=headers_factory(),
        )

        assert resp.status_code == 422
        assert _stored(db_conn, TENANT_1) is None


class TestScore:
    def test_defaults_score_from_retention_and_residency_only(
        self, migrated_client, headers_factory
    ) -> None:
        resp = migrated_client.get("/api/auth/compliance/score", headers=headers_factory())

        assert resp.status_code == 200
        body = resp.json()
        # protection 20 (retention>0, no lock), residency 100, access 0
        assert body["score"] == 38.0
        assert {p["id"]: p["status"] for p in body["policies"]}["pol-pii"] == "PENDING_SCAN"

    def test_every_control_on_scores_100(self, migrated_client, headers_factory, db_conn) -> None:
        _seed(
            db_conn,
            TENANT_1,
            pii_masking=True,
            retention_days=30,
            retention_lock=True,
            zero_trust=True,
            audit_residency=True,
        )

        body = migrated_client.get("/api/auth/compliance/score", headers=headers_factory()).json()

        assert body["score"] == 100.0
        assert all(p["status"] == "COMPLIANT" for p in body["policies"])
