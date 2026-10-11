"""The audit database: hash chain of audit.audit_log (tab 3 D5.3) and append-only svc_audit (D5.2, BR-47)."""

from __future__ import annotations

import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, one

pytestmark = pytest.mark.integration


def _log(conn, org, action: str) -> None:
    with conn.cursor() as cur:
        # seq, prev_hash and hash are the trigger's: whatever the service sends is replaced.
        cur.execute(
            "INSERT INTO audit.audit_log (id, org_id, seq, actor_kind, service, action, prev_hash, hash) "
            "VALUES (%s, %s, 99, 'user', 'qa', %s, 'x', 'x')",
            (str(new_id()), str(org), action),
        )


def test_chain_numbers_links_and_detects_tampering(audit_db):
    org, other = new_id(), new_id()
    as_role(audit_db, "svc_audit", org)
    for action in ("query.executed", "source.created", "access.denied"):
        _log(audit_db, org, action)
    as_admin(audit_db)
    as_role(audit_db, "svc_audit", other)
    _log(audit_db, other, "query.executed")
    as_admin(audit_db)
    with audit_db.cursor() as cur:
        cur.execute(
            "SELECT seq, prev_hash, hash FROM audit.audit_log WHERE org_id = %s ORDER BY seq",
            (str(org),),
        )
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]
    assert rows[0][1] == "0" * 64 and rows[1][1] == rows[0][2] and rows[2][1] == rows[1][2]
    assert one(audit_db, "SELECT seq FROM audit.audit_log WHERE org_id = %s", (str(other),)) == 1

    as_role(audit_db, "svc_audit", org)
    assert one(audit_db, "SELECT audit.verify_chain(%s, 1)", (str(org),)) is None
    as_admin(audit_db)
    # The cluster admin can still edit a row; the chain shows it.
    with audit_db.cursor() as cur:
        cur.execute(
            "UPDATE audit.audit_log SET action = 'source.deleted' WHERE org_id = %s AND seq = 2",
            (str(org),),
        )
    assert one(audit_db, "SELECT audit.verify_chain(%s, 1)", (str(org),)) == 2
    assert one(audit_db, "SELECT audit.verify_chain(%s, 3)", (str(org),)) is None
    with audit_db.cursor() as cur:
        cur.execute("DELETE FROM audit.audit_log WHERE org_id = %s AND seq = 3", (str(org),))
        # The hash does not depend on the session's time zone.
        cur.execute("SET LOCAL TimeZone = 'Asia/Ho_Chi_Minh'")
    assert one(audit_db, "SELECT audit.verify_chain(%s, 1)", (str(org),)) == 2
    assert one(audit_db, "SELECT audit.verify_chain(%s, 1)", (str(other),)) is None


def test_svc_audit_only_appends(audit_db):
    org = new_id()
    as_role(audit_db, "svc_audit", org)
    _log(audit_db, org, "query.executed")
    fails(audit_db, "UPDATE audit.audit_log SET action = 'x'", code="42501")
    fails(audit_db, "DELETE FROM audit.audit_log", code="42501")
    assert one(audit_db, "SELECT count(*) FROM audit.audit_log") == 1


_JOB = (
    "INSERT INTO audit.export_job (id, org_id, requested_by, row_count, status, approved_by)"
    " VALUES (%s, %s, %s, %s, %s, %s)"
)


def test_a_big_export_runs_only_counted_and_approved_by_another(audit_db):
    # BR-138: past 100,000 rows a second person approves; an uncounted job never runs.
    org, requester, approver = new_id(), new_id(), new_id()
    as_role(audit_db, "svc_audit", org)

    def job(rows, status, approved_by=None):
        approver_id = str(approved_by) if approved_by else None
        return (str(new_id()), str(org), str(requester), rows, status, approver_id)

    with audit_db.cursor() as cur:
        cur.execute(_JOB, job(90_000, "running"))
        cur.execute(_JOB, job(150_000, "running", approver))
    fails(audit_db, _JOB, job(None, "running"), code="23514")
    fails(audit_db, _JOB, job(150_000, "running"), code="23514")
    fails(audit_db, _JOB, job(150_000, "queued", requester), code="23514")
    fails(audit_db, _JOB, job(10, "ready"), code="23514")
