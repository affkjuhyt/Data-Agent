"""two_people: the one place that decides whether a caller may approve their own work."""

from __future__ import annotations

import uuid

import pytest

from services.api.kernel.approval import two_people
from services.api.kernel.auth import Principal
from services.api.kernel.problems import Problem
from services.api.modules.audit import public as audit_public


def _principal(self_approval: bool) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        external_id="sub",
        self_approval=self_approval,
        data_space_id=uuid.uuid4(),
    )


def test_someone_else_passes():
    assert two_people(_principal(False), uuid.uuid4(), message="m") is False


def test_self_refused_when_off():
    p = _principal(False)
    with pytest.raises(Problem) as exc:
        two_people(p, p.user_id, message="two people")
    assert exc.value.status == 403
    assert "two people" in str(exc.value.detail)


def test_self_allowed_when_on():
    p = _principal(True)
    assert two_people(p, uuid.uuid4(), p.user_id, message="m") is True


def test_none_is_ignored():
    assert two_people(_principal(False), None, message="m") is False


def test_other_person_is_not_a_self_approval():
    assert two_people(_principal(True), uuid.uuid4(), message="m") is False


def test_custom_status_and_code():
    p = _principal(False)
    with pytest.raises(Problem) as exc:
        two_people(p, p.user_id, message="m", status=422, code="business_rule_violated")
    assert exc.value.status == 422
    assert exc.value.code == "business_rule_violated"


async def test_record_writes_one_audit_row(monkeypatch):
    calls = []

    async def fake_append(org_id, **kwargs):
        calls.append((org_id, kwargs))

    monkeypatch.setattr(audit_public, "append", fake_append)
    p = _principal(True)
    oid = uuid.uuid4()
    await audit_public.record_self_approval(p, step="publish", object_kind="metric", object_id=oid)
    assert calls == [
        (
            p.org_id,
            {
                "actor_kind": "user",
                "actor_id": p.user_id,
                "on_behalf_of": None,
                "data_space_id": p.data_space_id,
                "action": "self_approval",
                "object_kind": "metric",
                "object_id": oid,
                "after": {"step": "publish"},
            },
        )
    ]
