"""worker-agent runs the decomposition skill (UC-03-01) on the bank of test_answers.py: semantic
queries through the Query engine, each a step with its query_run, and a result whose
contributions add up to the change."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import skill_runs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

pytestmark = [pytest.mark.integration]

# The bank's source schema, a module fixture of test_answers.py.
warehouse = test_answers.warehouse


def run_decomposition(
    bank: Bank, params: dict[str, Any], *, steps: list[dict[str, Any]] | None = None
) -> uuid.UUID:
    seed = bank.seed
    skill = seed.row(
        "skill.skill",
        org_id=bank.org,
        data_space_id=bank.space,
        code=f"phan_ra_{uuid.uuid4().hex[:6]}",
        name="Phân rã biến động",
        category="decomposition",
        scope="data_space",
        owner_id=bank.user,
        current_version=1,
    )
    # The api only runs a published version; this worker runs what it is given, so the version
    # skips the eval and approval a published one needs.
    version = seed.row(
        "skill.skill_version",
        org_id=bank.org,
        skill_id=skill,
        version=1,
        params_schema=json.dumps({"type": "object", "properties": {}}),
        steps=json.dumps(steps or [{"kind": "decompose"}]),
        status="testing",
    )
    run = seed.row(
        "skill.skill_run",
        org_id=bank.org,
        skill_version_id=version,
        data_space_id=bank.space,
        caller_kind="user",
        principal_id=bank.user,
        params=json.dumps(params),
    )
    seed.row(
        "skill.outbox",
        org_id=bank.org,
        aggregate_type="skill_run",
        aggregate_id=run,
        event_type="skill_run.requested",
        payload=json.dumps({"skill_run_id": str(run)}),
    )
    return run


async def drain() -> None:
    source = OutboxTableSource(schema="skill", event_types=frozenset(skill_runs.HANDLERS))
    while await run_once(source, skill_runs.HANDLERS, batch=50):
        pass


def outcome(seed: Seed, run: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, result, error FROM skill.skill_run WHERE id = %s", run
    )


@pytest.mark.req("AC-03-01-06")
async def test_ac_03_01_01_npl_by_region_decomposes_into_rate_and_mix(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = run_decomposition(bank, {"metric": "NPL_RATIO", "dimension": "vung", "period": "2026-09"})
    await drain()
    await drain()  # a redelivery finds the run finished and changes nothing
    status, result, error = outcome(seed, run)
    assert (status, error) == ("succeeded", None)
    # August: north 0/500. September: north 200/2000, south 100/1000 → 0% to 10%.
    assert result["summary"].startswith(
        "Tỷ lệ nợ xấu tăng từ 0,00% (08/2026) lên 10,00% (09/2026), +10,00 điểm %. "
        "Đóng góp lớn nhất theo vùng: Miền Bắc +6,67 điểm % (67% mức thay đổi). "
        "Trong đó +6,67 điểm % do tỷ lệ trong từng vùng thay đổi, +3,33 điểm % do cơ cấu"
    ), result["summary"]
    assert result["reconciled"] is True and "không phải nguyên nhân" in result["method"]
    assert [c["label"] for c in result["decomposition"]["contributions"]] == [
        "Miền Bắc",
        "Miền Nam",
    ]
    # Four queries (two measures by two months) then the decomposition, each a step; every query
    # went through the Query engine as this run's (AC-03-01-06).
    steps = seed.one(
        "SELECT json_agg(json_build_array(s.status, q.caller_kind, q.status) ORDER BY s.seq) "
        "FROM skill.skill_run_step s LEFT JOIN query.query_run q ON q.id = s.query_run_id "
        "WHERE s.skill_run_id = %s",
        run,
    )
    assert steps == [["ok", "skill_run", "succeeded"]] * 4 + [["ok", None, None]]
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_id = %s AND caller_kind = 'skill_run'",
            run,
        )
        == 4
    )


async def test_an_additive_metric_decomposes_by_its_own_change(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = run_decomposition(
        bank,
        {"metric": "DU_NO", "dimension": "vung", "period": "2026-09", "base_period": "2026-08"},
    )
    await drain()
    status, result, _ = outcome(seed, run)
    assert status == "succeeded"
    assert {c["member"]: float(c["value"]) for c in result["figures"]["contributions"]} == {
        "Miền Bắc": 1500.0,
        "Miền Nam": 1000.0,
    }
    assert result["shape"] == "additive" and "Trong đó" not in result["summary"]


@pytest.mark.req("AC-03-01-02")
async def test_a_long_run_tells_its_owner_when_it_ends_and_a_short_one_does_not(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    params = {"metric": "DU_NO", "dimension": "vung", "period": "2026-09"}
    quick = run_decomposition(bank, params)
    await drain()
    count = "SELECT count(*) FROM notify.notification WHERE source_ref = %s"
    assert seed.one(count, quick) == 0
    # Every run counts as long: it is background work, and the owner hears when it is done.
    monkeypatch.setattr(skill_runs, "NOTIFY_AFTER", datetime.timedelta(0))
    slow = run_decomposition(bank, params)
    await drain()
    assert seed.one(
        "SELECT user_id::text, kind, title, body FROM notify.notification WHERE source_ref = %s",
        slow,
    ) == (str(bank.user), "task_done", "Phân rã biến động: đã chạy xong", None)


@pytest.mark.req("AC-03-01-04")
async def test_a_dimension_without_data_is_skipped_and_said(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    entity = seed.one(
        "SELECT id FROM context.semantic_entity WHERE data_space_id = %s AND code = 'khoan_vay'",
        bank.space,
    )
    # A channel the source never fills: every row's member is empty.
    channel = seed.row(
        "context.semantic_dimension",
        org_id=bank.org,
        data_space_id=bank.space,
        entity_id=entity,
        code="kenh",
        name="kênh",
        expression="NULL::text",
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE context.metric_version SET allowed_dimensions = allowed_dimensions || %s::uuid "
            "WHERE metric_id = %s",
            (str(channel), str(bank.npl)),
        )
    run = run_decomposition(
        bank, {"metric": "NPL_RATIO", "dimension": "kenh,vung", "period": "2026-09"}
    )
    await drain()
    status, result, error = outcome(seed, run)
    assert (status, error) == ("succeeded", None)
    assert result["dimension"] == "vung" and result["skipped"] == ["chiều kênh"]
    assert result["summary"].endswith("Bỏ qua vì không có dữ liệu: chiều kênh.")


@pytest.mark.parametrize(
    ("params", "steps", "says"),
    [
        (
            {"metric": "NPL_RATIO", "dimension": "ngay", "period": "2026-09"},
            None,
            "không được phép",
        ),
        ({"metric": "NPL_RATIO", "dimension": "vung", "period": "9/2026"}, None, "YYYY-MM"),
        ({"metric": "KHONG_CO", "dimension": "vung", "period": "2026-09"}, None, "không có chỉ số"),
        (
            {"metric": "NPL_RATIO", "dimension": "vung", "period": "2026-09"},
            [{"kind": "python"}],
            "chưa chạy được",
        ),
    ],
)
async def test_what_it_cannot_run_fails_saying_why(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, Any],
    steps: list[dict[str, Any]] | None,
    says: str,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = run_decomposition(bank, params, steps=steps)
    await drain()
    status, result, error = outcome(seed, run)
    assert (status, result) == ("failed", None)
    assert says in error, error
