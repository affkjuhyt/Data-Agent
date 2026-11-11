"""data_product.use_requested: the pending access request POST /v1/data-products:request-use
promised (UC-11-07 through a data product).

The api answers with the request's id at once, but its context module connects as svc_context,
which may not write org; this opens the request as svc_org, under that id, the way
POST /v1/access-requests does (api org/admin/access.py): view_aggregate on the product's Data
Space until the date the api chose, and access_request.created for its steward. The id makes a
redelivered event a no-op.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import org
from services.worker_agent.events import Event, Handler

log = logging.getLogger(__name__)

ROLE = "svc_org"
USE_REQUESTED = "data_product.use_requested"


async def use_requested(event: Event) -> None:
    p, org_id = event.payload, event.org_id
    request_id = uuid.UUID(str(p["request_id"]))
    requester = uuid.UUID(str(p["requester_id"]))
    capability = str(p.get("capability") or "view_aggregate")
    async with scoped_session(ROLE, org_id, requester) as session:
        if await session.get(org.AccessRequest, request_id) is not None:
            return
        space = await session.get(org.DataSpace, uuid.UUID(str(p["owner_data_space_id"])))
        if space is None or space.deleted_at is not None or space.status == "archived":
            log.info("worker-agent | product use %s: its Data Space is gone", request_id)
            return
        row = org.AccessRequest(
            id=request_id,
            org_id=org_id,
            requester_id=requester,
            data_space_id=space.id,
            capability=capability,
            reason=f"Dùng data product {p.get('product_code') or p['product_id']}",
            requested_until=datetime.date.fromisoformat(str(p["requested_until"])),
            needs_compliance=capability == "view_detail",
            status="pending",
            created_by=requester,
            updated_by=requester,
        )
        session.add(row)
        session.add(
            org.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="access_request",
                aggregate_id=request_id,
                event_type="access_request.created",
                payload={
                    "access_request_id": str(request_id),
                    "data_space_id": str(space.id),
                    "requester_id": str(requester),
                    "capability": capability,
                    "needs_compliance": row.needs_compliance,
                    "steward_id": str(space.steward_id),
                    "backup_steward_id": (
                        str(space.backup_steward_id) if space.backup_steward_id else None
                    ),
                    "status": "pending",
                    "product_id": str(p["product_id"]),
                },
            )
        )


HANDLERS: dict[str, Handler] = {USE_REQUESTED: use_requested}
