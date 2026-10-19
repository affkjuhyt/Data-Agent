"""GET /v1/audit reads audit.audit_log through AuditLogView.

The ip column is PostgreSQL inet. SQLAlchemy hands that back as ipaddress.IPv4Address
or IPv6Address, and a page that contains one used to fail validation for the whole
response (the screen then shows the generic internal error).
"""

from __future__ import annotations

import datetime
import ipaddress
import uuid
from types import SimpleNamespace

import pytest

from services.api.modules.audit.schemas import AuditLogView

pytestmark = pytest.mark.unit


def _row(ip: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("01920000-0000-7000-8000-0000000000a1"),
        seq=1,
        occurred_at=datetime.datetime(2026, 10, 17, tzinfo=datetime.UTC),
        actor_kind="user",
        actor_id=None,
        on_behalf_of=None,
        data_space_id=None,
        service="api",
        action="api.listAuditLog",
        object_kind=None,
        object_id=None,
        outcome="ok",
        before=None,
        after={"status": 200},
        agent_run_id=None,
        ip=ip,
        prev_hash="0" * 64,
        hash="a" * 64,
    )


def test_inet_address_is_text_on_the_wire() -> None:
    view = AuditLogView.model_validate(_row(ipaddress.IPv4Address("192.168.194.1")))
    assert view.ip == "192.168.194.1"
    assert AuditLogView.model_validate(_row(ipaddress.IPv6Address("::1"))).ip == "::1"


def test_missing_or_textual_ip_stays_as_it_is() -> None:
    assert AuditLogView.model_validate(_row(None)).ip is None
    assert AuditLogView.model_validate(_row("10.0.0.8")).ip == "10.0.0.8"
