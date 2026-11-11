"""POST /v1/data-products:request-use promises a pending access request per product, under the id
it answers with; the api writes it as data_product.use_requested in context.outbox, and
worker-agent opens the request in org as svc_org (the context module's role cannot write org)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent import product_use
from services.worker_agent.events import Event

from .conftest import Seed

pytestmark = [pytest.mark.integration]


def _world(seed: Seed) -> dict[str, uuid.UUID]:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức")
    users = {
        name: seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}-{uuid.uuid4().hex[:6]}",
            username=f"{name}-{uuid.uuid4().hex[:6]}",
            display_name=name,
        )
        for name in ("steward", "requester")
    }
    owner = seed.row(
        "org.data_space", org_id=org, code="DS-BAN", name="Bán hàng", steward_id=users["steward"]
    )
    mine = seed.row(
        "org.data_space", org_id=org, code="DS-TC", name="Tài chính", steward_id=users["requester"]
    )
    return {"org": org, "owner": owner, "mine": mine, **users}


def _event(w: dict[str, uuid.UUID], request_id: uuid.UUID, until: datetime.date) -> Event:
    product = uuid.uuid4()
    return Event(
        uuid.uuid4(),
        w["org"],
        product_use.USE_REQUESTED,
        "data_product",
        product,
        {
            "request_id": str(request_id),
            "product_id": str(product),
            "product_code": "doanh_thu",
            "owner_data_space_id": str(w["owner"]),
            "requester_data_space_id": str(w["mine"]),
            "requester_id": str(w["requester"]),
            "capability": "view_aggregate",
            "requested_until": until.isoformat(),
        },
    )


async def test_the_promised_request_is_opened_once_under_its_id(seed: Seed) -> None:
    w = _world(seed)
    request_id = uuid.uuid4()
    until = datetime.date.today() + datetime.timedelta(days=90)
    event = _event(w, request_id, until)

    await product_use.use_requested(event)
    # A redelivered event opens nothing more.
    await product_use.use_requested(event)

    assert seed.all(
        "SELECT data_space_id, requester_id, capability, reason, requested_until, status,"
        " needs_compliance, created_by FROM org.access_request WHERE org_id = %s",
        w["org"],
    ) == [
        (
            w["owner"],
            w["requester"],
            "view_aggregate",
            "Dùng data product doanh_thu",
            until,
            "pending",
            False,
            w["requester"],
        )
    ]
    assert seed.one("SELECT id FROM org.access_request WHERE org_id = %s", w["org"]) == request_id
    created = seed.all(
        "SELECT aggregate_id, payload FROM org.outbox WHERE org_id = %s"
        " AND event_type = 'access_request.created'",
        w["org"],
    )
    assert len(created) == 1
    assert created[0][0] == request_id
    assert created[0][1]["steward_id"] == str(w["steward"])
    assert created[0][1]["data_space_id"] == str(w["owner"])
    assert created[0][1]["status"] == "pending"
