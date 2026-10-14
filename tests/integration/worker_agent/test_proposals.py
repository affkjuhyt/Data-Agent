"""Proposals opened from a module's event and decisions applied back to its object: skill publish
and share (UC-03-05, UC-03-07) and pack activation (UC-09-02)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from services.worker_agent import proposals
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed

pytestmark = [pytest.mark.integration]


@dataclass
class World:
    seed: Seed
    org: uuid.UUID
    space: uuid.UUID
    author: uuid.UUID
    steward: uuid.UUID


@pytest.fixture
def world(seed: Seed) -> World:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ngân hàng thử")
    author = seed.row(
        "org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A"
    )
    steward = seed.row(
        "org.app_user", org_id=org, external_id="kc-s", username="s", display_name="S"
    )
    space = seed.row(
        "org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=steward
    )
    return World(seed, org, space, author, steward)


def _event(w: World, schema: str, event_type: str, aggregate: uuid.UUID, **payload: Any) -> None:
    w.seed.row(
        f"{schema}.outbox",
        org_id=w.org,
        aggregate_type=event_type.split(".")[0],
        aggregate_id=aggregate,
        event_type=event_type,
        payload=json.dumps(payload, default=str),
    )


async def _drain(schema: str) -> None:
    source = OutboxTableSource(schema=schema, event_types=frozenset(proposals.HANDLERS))
    while await run_once(source, proposals.HANDLERS, batch=50):
        pass


def _decide(w: World, proposal: uuid.UUID, decision: str) -> None:
    """What the api's POST /v1/proposals/{id}:decide writes, reduced to the rows the worker reads."""
    status = {"accept": "accepted", "reject": "rejected", "request_changes": "open"}[decision]
    w.seed.one("UPDATE inbox.proposal SET status = %s WHERE id = %s RETURNING 1", status, proposal)
    _event(
        w,
        "inbox",
        proposals.DECIDED,
        proposal,
        proposal_id=proposal,
        status=status,
        decision=decision,
        reviewer_id=w.steward,
    )


def _skill(w: World, *, published: bool = False) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    skill_id = w.seed.row(
        "skill.skill",
        org_id=w.org,
        data_space_id=w.space,
        code=f"sk-{uuid.uuid4().hex[:6]}",
        name="Phân rã nợ xấu",
        category="decomposition",
        owner_id=w.author,
        scope="data_space",
    )
    old = None
    if published:
        old = w.seed.row(
            "skill.skill_version",
            org_id=w.org,
            skill_id=skill_id,
            version=1,
            params_schema="{}",
            steps="[]",
            status="published",
            eval_run_id=uuid.uuid4(),
            approved_by=w.steward,
        )
    version = w.seed.row(
        "skill.skill_version",
        org_id=w.org,
        skill_id=skill_id,
        version=2,
        params_schema="{}",
        steps="[]",
        status="pending_review",
        eval_run_id=uuid.uuid4(),
    )
    return skill_id, version, old


def _request_publish(
    w: World, skill_id: uuid.UUID, version: uuid.UUID, steps: int = 1
) -> uuid.UUID:
    _event(
        w,
        "skill",
        proposals.PUBLISH,
        version,
        skill_version_id=version,
        skill_id=skill_id,
        version=2,
        scope="data_space",
        data_space_id=w.space,
        approval_steps=steps,
        eval_run_id=uuid.uuid4(),
        requested_by=w.author,
        status="pending_review",
    )
    return version


def _opened(w: World, target: uuid.UUID) -> tuple[Any, ...]:
    return w.seed.one(  # type: ignore[return-value]
        "SELECT id, kind, status, proposed_by::text FROM inbox.proposal WHERE target_ref = %s",
        target,
    )


async def test_uc_03_05_accepting_the_proposal_publishes_the_version(world: World) -> None:
    w = world
    skill_id, version, old = _skill(w, published=True)
    _request_publish(w, skill_id, version)
    await _drain("skill")
    await _drain("skill")  # redelivery-proof: still one proposal
    assert w.seed.one("SELECT count(*) FROM inbox.proposal WHERE target_ref = %s", version) == 1
    pid, kind, status, by = _opened(w, version)
    assert (kind, status, by) == ("skill_publish", "open", str(w.author))

    _decide(w, pid, "accept")
    await _drain("inbox")
    assert w.seed.one(
        "SELECT status, approved_by::text FROM skill.skill_version WHERE id = %s", version
    ) == ("published", str(w.steward))
    assert w.seed.one("SELECT status FROM skill.skill_version WHERE id = %s", old) == "retired"
    assert w.seed.one("SELECT current_version FROM skill.skill WHERE id = %s", skill_id) == 2
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "applied"
    assert (
        w.seed.one(
            "SELECT count(*) FROM skill.outbox WHERE aggregate_id = %s AND event_type = 'skill.published'",
            version,
        )
        == 1
    )


async def test_ac_03_05_02_changes_asked_send_the_version_back_to_draft(world: World) -> None:
    w = world
    skill_id, version, _ = _skill(w)
    _request_publish(w, skill_id, version)
    await _drain("skill")
    pid = _opened(w, version)[0]
    _decide(w, pid, "request_changes")
    await _drain("inbox")
    assert w.seed.one("SELECT status FROM skill.skill_version WHERE id = %s", version) == "draft"
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "withdrawn"


async def test_br_96_a_two_step_publish_is_not_opened_with_one_approver(world: World) -> None:
    w = world
    skill_id, version, _ = _skill(w)
    _request_publish(w, skill_id, version, steps=2)
    await _drain("skill")
    assert w.seed.one("SELECT count(*) FROM inbox.proposal WHERE target_ref = %s", version) == 0
    assert w.seed.one("SELECT status FROM skill.skill_version WHERE id = %s", version) == "testing"


async def test_uc_03_07_an_accepted_share_widens_the_scope(world: World) -> None:
    w = world
    skill_id, version, _ = _skill(w)
    w.seed.one(
        "UPDATE skill.skill_version SET status = 'published', approved_by = %s WHERE id = %s"
        " RETURNING 1",
        w.steward,
        version,
    )
    w.seed.one("UPDATE skill.skill SET scope = 'personal' WHERE id = %s RETURNING 1", skill_id)
    _event(
        w,
        "skill",
        proposals.SHARE,
        skill_id,
        skill_id=skill_id,
        skill_version_id=version,
        version=2,
        data_space_id=w.space,
        previous_scope="personal",
        scope="data_space",
        approval_steps=1,
        requested_by=w.author,
    )
    await _drain("skill")
    pid = _opened(w, version)[0]
    assert w.seed.one("SELECT scope FROM skill.skill WHERE id = %s", skill_id) == "personal"
    _decide(w, pid, "accept")
    await _drain("inbox")
    assert w.seed.one("SELECT scope FROM skill.skill WHERE id = %s", skill_id) == "data_space"
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "applied"


def _install(w: World) -> uuid.UUID:
    pack_id = w.seed.row(
        "pack.industry_pack",
        org_id=w.org,
        code=f"bank-{uuid.uuid4().hex[:6]}",
        name="Ngân hàng",
        industry_code="bank",
        publisher="Tổ chức",
        origin="organisation",
    )
    version = w.seed.row(
        "pack.pack_version",
        org_id=w.org,
        pack_id=pack_id,
        version="2026.3",
        manifest="{}",
        signature="-",
        min_core_version="3.0",
        object_key="packs/bank/2026.3",
    )
    install = w.seed.row(
        "pack.pack_install",
        org_id=w.org,
        data_space_id=w.space,
        pack_version_id=version,
        status="pending_activation",
    )
    _event(
        w,
        "pack",
        proposals.ACTIVATE,
        install,
        pack_install_id=install,
        data_space_id=w.space,
        pack_version_id=version,
        calibration_run_id=uuid.uuid4(),
        proposal_kind="pack_activate",
        status="pending_activation",
        requested_by=w.author,
    )
    return install


async def test_uc_09_02_an_accepted_activation_activates_the_install(world: World) -> None:
    w = world
    install = _install(w)
    await _drain("pack")
    pid = _opened(w, install)[0]
    assert str(
        w.seed.one("SELECT proposal_id FROM pack.pack_install WHERE id = %s", install)
    ) == str(pid)
    _decide(w, pid, "accept")
    await _drain("inbox")
    assert w.seed.one(
        "SELECT status, activated_by::text FROM pack.pack_install WHERE id = %s", install
    ) == ("active", str(w.steward))
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "applied"


async def test_ac_09_02_05_an_activation_waits_for_a_closing_period(world: World) -> None:
    w = world
    install = _install(w)
    await _drain("pack")
    pid = _opened(w, install)[0]
    close = w.seed.row(
        "period.period_close",
        org_id=w.org,
        data_space_id=w.space,
        period_label="10/2026",
        business_date="2026-10-31",
        status="closing",
    )
    _decide(w, pid, "accept")
    await _drain("inbox")
    assert (
        w.seed.one("SELECT status FROM pack.pack_install WHERE id = %s", install)
        == "pending_activation"
    )
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "accepted"
    # The close ends: activated without another approval.
    w.seed.one(
        "UPDATE period.period_close SET status = 'close_failed' WHERE id = %s RETURNING 1", close
    )
    assert await proposals.activate_waiting(w.org, w.space, w.steward) == 1
    assert w.seed.one("SELECT status FROM pack.pack_install WHERE id = %s", install) == "active"
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "applied"


async def test_uc_06_04_writeback_opens_and_applies_a_real_proposal(world: World) -> None:
    w = world
    source = w.seed.row(
        "catalog.source",
        org_id=w.org,
        data_space_id=w.space,
        name="Core banking",
        kind="postgresql",
        secret_ref=f"kv/sources/{uuid.uuid4()}",
        writeback_secret_ref=f"kv/writeback/{uuid.uuid4()}",
        writeback_tables=["core.du_no_dieu_chinh"],
    )
    w.seed.row(
        "catalog.dataset",
        org_id=w.org,
        data_space_id=w.space,
        source_id=source,
        origin="source",
        schema_name="core",
        table_name="du_no",
        display_name="Dư nợ",
        enabled=True,
    )
    derived = w.seed.row(
        "catalog.dataset",
        org_id=w.org,
        data_space_id=w.space,
        origin="derived",
        schema_name="ws_tin_dung",
        table_name="du_no_dieu_chinh",
        display_name="Dư nợ điều chỉnh",
        enabled=True,
    )
    writeback = w.seed.row(
        "pipeline.writeback_request",
        org_id=w.org,
        data_space_id=w.space,
        source_id=source,
        target_table="core.du_no_dieu_chinh",
        from_dataset_id=derived,
        requested_by=w.author,
        diff_summary=json.dumps({"inserts": 4, "updates": 2, "deletes": 0}),
        status="submitted",
        proposal_id=uuid.uuid4(),
    )
    proposal = w.seed.one(
        "SELECT proposal_id FROM pipeline.writeback_request WHERE id = %s", writeback
    )
    _event(
        w,
        "pipeline",
        proposals.WRITEBACK,
        writeback,
        writeback_request_id=writeback,
        proposal_id=proposal,
        data_space_id=w.space,
        source_id=source,
        target_table="core.du_no_dieu_chinh",
        from_dataset_id=derived,
        diff_summary={"inserts": 4, "updates": 2, "deletes": 0},
        requested_by=w.author,
    )
    await _drain("pipeline")
    assert _opened(w, writeback)[:3] == (proposal, "writeback", "open")
    _decide(w, proposal, "accept")
    await _drain("inbox")
    assert w.seed.one(
        "SELECT status, approved_by::text FROM pipeline.writeback_request WHERE id = %s", writeback
    ) == ("approved", str(w.steward))
    assert w.seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "applied"


async def test_an_edited_diff_never_widens_what_is_applied(world: World) -> None:
    # accept_with_edit replaces the diff; a reviewer must not turn a Data Space share into an
    # org-wide one (BR-96 wants a second, administrator step for that).
    w = world
    skill_id, version, _ = _skill(w)
    w.seed.one(
        "UPDATE skill.skill_version SET status = 'published', approved_by = %s WHERE id = %s"
        " RETURNING 1",
        w.steward,
        version,
    )
    w.seed.one("UPDATE skill.skill SET scope = 'personal' WHERE id = %s RETURNING 1", skill_id)
    _event(
        w,
        "skill",
        proposals.SHARE,
        skill_id,
        skill_id=skill_id,
        skill_version_id=version,
        version=2,
        data_space_id=w.space,
        previous_scope="personal",
        scope="data_space",
        approval_steps=1,
        requested_by=w.author,
    )
    await _drain("skill")
    pid = _opened(w, version)[0]
    w.seed.one(
        "UPDATE inbox.proposal SET diff = %s WHERE id = %s RETURNING 1",
        json.dumps({"action": "share", "skill_id": str(skill_id), "scope": "org"}),
        pid,
    )
    _decide(w, pid, "accept")
    await _drain("inbox")
    assert w.seed.one("SELECT scope FROM skill.skill WHERE id = %s", skill_id) == "data_space"
