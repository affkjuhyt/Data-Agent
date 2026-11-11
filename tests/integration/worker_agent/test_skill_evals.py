"""worker-agent tests a skill version (UC-03-04) on the bank of test_answers.py: each case of the
skill's eval set is a skill run of the version (caller_kind eval), scored against what it
expects; a version at the bar keeps `testing` with its eval run, one below goes back to draft,
and a run that has nothing to score ends `failed` instead of leaving its client polling."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import skill_evals
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse

PARAMS = {"metric": "DU_NO", "dimension": "vung", "period": "2026-09", "base_period": "2026-08"}
# test_skill_runs.py: September against August, North +1500 and South +1000.
RIGHT = {"contributions": {"Miền Bắc": 1500, "Miền Nam": 1000}}
WRONG = {"contributions": {"Miền Bắc": 1501}}


def skill_version(
    bank: Bank, *, status: str = "testing", steps: list[dict[str, Any]] | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    seed = bank.seed
    skill = seed.row(
        "skill.skill",
        org_id=bank.org,
        data_space_id=bank.space,
        code=f"phan_ra_{uuid.uuid4().hex[:6]}",
        name="Phân rã dư nợ",
        category="decomposition",
        scope="data_space",
        owner_id=bank.user,
        current_version=1,
    )
    version = seed.row(
        "skill.skill_version",
        org_id=bank.org,
        skill_id=skill,
        version=1,
        params_schema=json.dumps({"type": "object", "properties": {}}),
        steps=json.dumps(steps or [{"kind": "decompose"}]),
        status=status,
        # A needs_review version was published: approved, and passed by an earlier run.
        **(
            {"approved_by": bank.user, "eval_run_id": uuid.uuid4()}
            if status == "needs_review"
            else {}
        ),
    )
    return skill, version


def eval_set(
    bank: Bank,
    skill: uuid.UUID,
    cases: list[dict[str, Any]],
    *,
    min_items: int,
    author: uuid.UUID | None = None,
) -> uuid.UUID:
    made = bank.seed.row(
        "eval.eval_set",
        org_id=bank.org,
        data_space_id=bank.space,
        subject_type="skill",
        subject_id=skill,
        name="Ca kiểm thử phân rã",
        min_items=min_items,
        pass_threshold=1,
    )
    for expected in cases:
        bank.seed.row(
            "eval.eval_item",
            org_id=bank.org,
            eval_set_id=made,
            input=json.dumps({"params": PARAMS}),
            expected=json.dumps(expected),
            created_by=author or bank.user,
            updated_by=author or bank.user,
        )
    return made


def request_test(bank: Bank, skill: uuid.UUID, version: uuid.UUID, previous: str) -> uuid.UUID:
    run = uuid.uuid4()
    bank.seed.row(
        "skill.outbox",
        org_id=bank.org,
        aggregate_type="skill_version",
        aggregate_id=version,
        event_type=skill_evals.EVENT_TYPE,
        payload=json.dumps(
            {
                "skill_version_id": str(version),
                "skill_id": str(skill),
                "version": 1,
                "data_space_id": str(bank.space),
                "eval_run_id": str(run),
                "previous_status": previous,
            }
        ),
    )
    return run


async def drain() -> None:
    source = OutboxTableSource(schema="skill", event_types=frozenset(skill_evals.HANDLERS))
    while await run_once(source, skill_evals.HANDLERS, batch=50):
        pass


def version_state(seed: Seed, version: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, eval_run_id::text FROM skill.skill_version WHERE id = %s", version
    )


def run_state(seed: Seed, run: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, item_count, passed, failed FROM eval.eval_run WHERE id = %s", run
    )


def told(seed: Seed, run: uuid.UUID) -> list[tuple[Any, ...]]:
    return seed.all(
        "SELECT user_id::text, kind, body FROM notify.notification WHERE source_ref = %s", run
    )


@pytest.mark.req("AC-03-04-01")
async def test_a_version_at_the_bar_keeps_testing_with_its_eval_run(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank)
    eval_set(
        bank, skill, [RIGHT, {"figures": {"change": 2500}}, {"dimension": "vung"}], min_items=3
    )
    run = request_test(bank, skill, version, "draft")
    await drain()
    assert run_state(seed, run) == ("completed", 3, 3, 0)
    assert version_state(seed, version) == ("testing", str(run))
    # Each case ran as a skill run of the version, called by the eval run.
    assert (
        seed.one(
            "SELECT count(*) FROM skill.skill_run WHERE caller_kind = 'eval' AND caller_ref = %s "
            "AND skill_version_id = %s AND status = 'succeeded'",
            run,
            version,
        )
        == 3
    )
    tested = seed.one(
        "SELECT payload FROM skill.outbox WHERE aggregate_id = %s "
        "AND event_type = 'skill_version.tested'",
        version,
    )
    assert tested["passed"] is True and tested["status"] == "testing"
    assert [(u, k) for u, k, _ in told(seed, run)] == [(str(bank.user), "task_done")]
    # A redelivery changes nothing and tells no one again.
    seed.all(
        "UPDATE skill.outbox SET published_at = NULL WHERE aggregate_id = %s "
        "AND event_type = %s RETURNING id",
        version,
        skill_evals.EVENT_TYPE,
    )
    await drain()
    assert version_state(seed, version) == ("testing", str(run)) and len(told(seed, run)) == 1


@pytest.mark.req("AC-03-04-01")
async def test_a_failing_case_sends_the_version_back_to_draft(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank)
    eval_set(bank, skill, [RIGHT, WRONG, RIGHT], min_items=3)
    run = request_test(bank, skill, version, "draft")
    await drain()
    assert run_state(seed, run) == ("completed", 3, 2, 1)
    assert version_state(seed, version) == ("draft", None)
    causes = seed.all(
        "SELECT passed, cause FROM eval.eval_result WHERE eval_run_id = %s ORDER BY passed", run
    )
    assert causes[0] == (False, "the result's value is not what the item expects")
    ((_, kind, body),) = told(seed, run)
    assert kind == "alert" and "2/3" in body and body.endswith("Phiên bản đã về nháp.")


async def test_without_an_eval_set_the_run_fails_saying_so(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank)
    run = request_test(bank, skill, version, "draft")
    await drain()
    assert run_state(seed, run) == ("failed", 0, 0, 0)
    assert version_state(seed, version) == ("draft", None)
    # The run hangs on the skill's own (empty) set, the one its author adds cases to.
    assert seed.one(
        "SELECT s.subject_type, s.subject_id::text, s.data_space_id::text FROM eval.eval_run r "
        "JOIN eval.eval_set s ON s.id = r.eval_set_id WHERE r.id = %s",
        run,
    ) == ("skill", str(skill), str(bank.space))
    # The set it makes holds the bar :publish holds (BR-12), not a lower one.
    assert (
        seed.one(
            "SELECT s.min_items FROM eval.eval_run r JOIN eval.eval_set s ON s.id = r.eval_set_id "
            "WHERE r.id = %s",
            run,
        )
        == skill_evals.MIN_PASSING_CASES
        == 3
    )
    ((_, kind, body),) = told(seed, run)
    assert kind == "alert" and body.startswith("Kỹ năng chưa có bộ đánh giá")


async def test_steps_the_worker_cannot_run_fail_the_run_without_running_a_case(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank, steps=[{"kind": "python"}])
    eval_set(bank, skill, [RIGHT], min_items=1)
    run = request_test(bank, skill, version, "draft")
    await drain()
    assert run_state(seed, run) == ("failed", 1, 0, 0)
    assert version_state(seed, version) == ("draft", None)
    assert seed.one("SELECT count(*) FROM skill.skill_run WHERE caller_ref = %s", run) == 0
    ((_, _, body),) = told(seed, run)
    assert "chưa chạy được" in body


async def test_a_needs_review_version_that_passes_is_published_again(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank, status="needs_review")
    eval_set(bank, skill, [RIGHT, RIGHT, RIGHT], min_items=1)
    run = request_test(bank, skill, version, "needs_review")
    await drain()
    assert version_state(seed, version) == ("published", str(run))


async def test_a_needs_review_version_below_br_12_is_not_published_by_the_worker(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The set's own bar (one case) is met, :publish's is not: a data_space skill needs three
    # passing cases (BR-12), so the version keeps its label rather than going out on one.
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank, status="needs_review")
    eval_set(bank, skill, [RIGHT], min_items=1)
    before = version_state(seed, version)
    run = request_test(bank, skill, version, "needs_review")
    await drain()
    assert run_state(seed, run) == ("completed", 1, 1, 0)
    assert version_state(seed, version) == before
    ((_, kind, body),) = told(seed, run)
    assert kind == "alert" and "ít nhất 3 ca đạt" in body


async def test_only_the_owners_cases_run_with_the_owners_rights(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A case written, or last edited, by someone else would read the owner's data for them.
    bank = build(seed, warehouse, monkeypatch)
    other = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-x", username="x", display_name="X"
    )
    skill, version = skill_version(bank)
    made = eval_set(bank, skill, [RIGHT], min_items=1)
    eval_set_items(bank, made, [RIGHT], created_by=other, updated_by=other)
    eval_set_items(bank, made, [WRONG], created_by=bank.user, updated_by=other)
    run = request_test(bank, skill, version, "draft")
    await drain()
    assert run_state(seed, run) == ("completed", 1, 1, 0)
    assert seed.one("SELECT count(*) FROM skill.skill_run WHERE caller_ref = %s", run) == 1
    ((_, _, body),) = told(seed, run)
    assert "2 ca do người khác viết hoặc sửa không chạy" in body


def eval_set_items(
    bank: Bank, made: uuid.UUID, cases: list[dict[str, Any]], **authors: uuid.UUID
) -> None:
    for expected in cases:
        bank.seed.row(
            "eval.eval_item",
            org_id=bank.org,
            eval_set_id=made,
            input=json.dumps({"params": PARAMS}),
            expected=json.dumps(expected),
            **authors,
        )


async def test_a_needs_review_version_that_fails_keeps_running_with_its_label(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    skill, version = skill_version(bank, status="needs_review")
    eval_set(bank, skill, [WRONG], min_items=1)
    before = version_state(seed, version)
    run = request_test(bank, skill, version, "needs_review")
    await drain()
    assert version_state(seed, version) == before
    ((_, kind, _),) = told(seed, run)
    assert kind == "alert"
