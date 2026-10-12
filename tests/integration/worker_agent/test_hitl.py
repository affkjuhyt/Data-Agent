"""worker-agent opens the HITL requests other modules ask for (hitl.requested), from the main
database's outboxes and the audit database's, once however often an event is delivered."""

from __future__ import annotations

import json
import uuid

import psycopg2
import pytest

from services.worker_agent.main import HANDLERS, run_once, sources

from .conftest import Seed

pytestmark = [pytest.mark.integration]


def _payload(request_id: uuid.UUID, **extra: object) -> str:
    # The contract of services/api/modules/inbox/public.py.
    body: dict[str, object] = {
        "hitl_request_id": str(request_id),
        "data_space_id": None,
        "kind": "period_exception",
        "risk": "high",
        "title": "Ngoại lệ đối soát NPL_RATIO",
        "context": {"item_code": "NPL_RATIO"},
        "options": [],
        "source_service": "period",
        "source_ref": str(uuid.uuid4()),
        "created_by": None,
        "assignee_id": None,
        "assignee_group": None,
        "assignee_capability": "steward",
        "approval_steps": ["steward", "compliance"],
        "due_at": None,
        "parent_run_label": None,
    }
    body.update(extra)
    return json.dumps(body)


async def _drain() -> None:
    for source in await sources():
        while await run_once(source, HANDLERS, batch=50):
            pass


async def test_a_requested_decision_becomes_one_request_with_its_chain(seed: Seed) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")
    owner = seed.row("org.app_user", org_id=org, external_id="kc-o", username="o", display_name="O")
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=owner)
    request = uuid.uuid4()
    payload = _payload(request, data_space_id=str(space), created_by=str(owner))
    for _ in range(2):  # delivered twice
        seed.row(
            "period.outbox",
            org_id=org,
            aggregate_type="period_reconciliation",
            aggregate_id=uuid.uuid4(),
            event_type="hitl.requested",
            payload=payload,
        )
    await _drain()
    assert seed.one(
        "SELECT kind, assignee_capability, data_space_id::text FROM inbox.hitl_request WHERE id = %s",
        request,
    ) == ("period_exception", "steward", str(space))
    assert seed.one(
        "SELECT array_agg(approver_role ORDER BY step) FROM inbox.hitl_approval_step "
        "WHERE hitl_request_id = %s",
        request,
    ) == ["steward", "compliance"]
    assert (
        seed.one(
            "SELECT count(*) FROM inbox.outbox WHERE aggregate_id = %s AND event_type = 'hitl.opened'",
            request,
        )
        == 1
    )


async def test_an_org_wide_request_from_the_audit_database(
    seed: Seed, ddl_server: dict[str, str]
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")
    request = uuid.uuid4()
    audit = psycopg2.connect(ddl_server["dataagent_audit"])
    audit.autocommit = True
    with audit.cursor() as cur:
        cur.execute(
            "INSERT INTO audit.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (%s, %s, 'export_job', %s, 'hitl.requested', %s)",
            (
                str(uuid.uuid4()),
                str(org),
                str(uuid.uuid4()),
                _payload(
                    request,
                    kind="export",
                    source_service="audit",
                    assignee_capability="compliance",
                    approval_steps=[],
                ),
            ),
        )
    audit.close()
    await _drain()
    assert seed.one(
        "SELECT kind, data_space_id FROM inbox.hitl_request WHERE id = %s", request
    ) == ("export", None)


async def test_an_approved_audit_export_is_released_once(
    seed: Seed, ddl_server: dict[str, str]
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")
    requester, approver = uuid.uuid4(), uuid.uuid4()
    job, hitl_id = uuid.uuid4(), uuid.uuid4()
    audit = psycopg2.connect(ddl_server["dataagent_audit"])
    audit.autocommit = True
    with audit.cursor() as cur:
        cur.execute(
            "INSERT INTO audit.export_job (id, org_id, filters, row_count, status, requested_by, "
            "hitl_request_id) VALUES (%s, %s, '{}', 200000, 'waiting_approval', %s, %s)",
            (str(job), str(org), str(requester), str(hitl_id)),
        )
    resolved = json.dumps(
        {
            "hitl_request_id": str(hitl_id),
            "kind": "export",
            "status": "approved",
            "decision": "approve",
            "source_service": "audit",
            "source_ref": str(job),
            "resolved_by": str(approver),
        }
    )
    for _ in range(2):  # delivered twice
        seed.row(
            "inbox.outbox",
            org_id=org,
            aggregate_type="hitl_request",
            aggregate_id=hitl_id,
            event_type="hitl.resolved",
            payload=resolved,
        )
    await _drain()
    with audit.cursor() as cur:
        cur.execute(
            "SELECT status, approved_by::text FROM audit.export_job WHERE id = %s", (str(job),)
        )
        assert cur.fetchone() == ("queued", str(approver))
    audit.close()
