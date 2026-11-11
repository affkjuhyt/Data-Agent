"""A skill version's eval run (UC-03-04): the `skill_version.eval_requested` handler.

POST /v1/skill-versions/{id}:test moves a draft to `testing` (a needs_review version is re-tested
in place) and reserves the eval run's id. Here the run is made under that id on the skill's own
eval set (eval.eval_set subject_type skill, subject_id the skill), each active item is run as a
skill run of the version (caller_kind eval, through skill_runs, so the numbers come from the same
Query engine path as any run), scored against what the item expects, and the bar is applied as
for an agent version (agent_evals.py): at least the set's `min_items` items and a pass rate of at
least its `pass_threshold`.

At the bar, `skill_version.eval_run_id` is the run (a testing version stays `testing`, ready for
:publish; a needs_review one is published again, its label gone). Below it, a testing version goes
back to draft (AC-03-04-01) and a needs_review one keeps running with its label (UC-03-04 1a).
Either way the owner is told, and skill.outbox gets `skill_version.tested`.

A skill item holds its parameters in `input` ({"params": {...}}, or the parameters themselves)
and in `expected` any of: `figures` {base|current|change: number}, `contributions`
{member: number}, `dimension` (the code the result is cut by), `text_contains` [..] (in the
summary), or `behavior` "refuse" (the run must fail). A number matches within the item's
`tolerance` (absolute, as eval_runs.py reads it) or else RELATIVE_TOLERANCE (BR-95,
AC-03-04-04: 0.005 % off passes, 0.02 % off fails).

A run never hangs on what cannot be scored: no eval set, an empty one, or steps this worker does
not run end the run `failed` (eval_run has no column for why: the owner's notification says it).
eval_run.eval_set_id is required, so a skill without a set gets an empty one made here, the set
its author then adds cases to (POST /v1/eval-sets/{id}/items); no other route makes a skill's set.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import func, select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import eval as ev
from libs.dataagent_db.models import skill
from services.worker_agent import eval_runs, skill_runs
from services.worker_agent.events import Event, Handler
from services.worker_agent.runs import _tell

logger = logging.getLogger(__name__)

EVENT_TYPE = "skill_version.eval_requested"
SKILL_ROLE = "svc_skill"
EVAL_ROLE = eval_runs.ROLE
NO_SET = "Kỹ năng chưa có bộ đánh giá"
# BR-95 (AC-03-04-04): an amount 0.005 % off passes, 0.02 % off fails; a count off by one fails
# for any count under 10 000.
RELATIVE_TOLERANCE = decimal.Decimal("0.0001")


@dataclasses.dataclass(frozen=True)
class _Version:
    id: uuid.UUID
    number: int
    skill_id: uuid.UUID
    name: str
    category: str
    steps: list[Any]
    owner: uuid.UUID
    space: uuid.UUID | None


async def evaluate_skill(event: Event) -> None:
    run_id = uuid.UUID(str(event.payload["eval_run_id"]))
    version_id = uuid.UUID(str(event.payload["skill_version_id"]))
    async with scoped_session(SKILL_ROLE, event.org_id) as session:
        version = await session.get(skill.SkillVersion, version_id)
        row = await session.get(skill.Skill, version.skill_id) if version else None
        # Sent back or moved on since the request: there is nothing to test.
        if version is None or row is None or version.status not in ("testing", "needs_review"):
            return
        about = _Version(
            version.id,
            version.version,
            row.id,
            row.name,
            row.category,
            list(version.steps or []),
            row.owner_id,
            row.data_space_id,
        )

    reason = await _made(event, run_id, about)
    if reason is None:
        try:
            await _score(event, run_id, about)
        except Exception:
            # The run is made: a redelivery would find it `running` and run every case again,
            # each a skill run. End it here, never leave the client polling a run nobody works on.
            logger.exception("skill eval %s failed", run_id)
            reason = "Lỗi khi chạy các ca kiểm thử."
            await _end_failed(event.org_id, about.owner, run_id)
    await _gate(event, run_id, about, reason)


async def _made(event: Event, run_id: uuid.UUID, about: _Version) -> str | None:
    """Makes the run under its reserved id, or ends it failed at once. None when its cases are to
    be run now; else why they will not be (also for a run already made by an earlier delivery)."""
    async with scoped_session(EVAL_ROLE, event.org_id, about.owner) as session:
        existing = await session.get(ev.EvalRun, run_id)
        if existing is not None:
            # Handled before: only the gate is applied again (it reads the run as it ended).
            return "" if existing.status != "running" else None
        found = await session.scalar(
            select(ev.EvalSet)
            .where(
                ev.EvalSet.status == "active",
                ev.EvalSet.subject_type == "skill",
                ev.EvalSet.subject_id == about.skill_id,
                ev.EvalSet.data_space_id.is_not_distinct_from(about.space),
            )
            .order_by(ev.EvalSet.created_at.desc(), ev.EvalSet.id)
            .limit(1)
        )
        reason: str | None = None
        if found is None:
            eval_set = ev.EvalSet(
                id=uuid7(),
                org_id=event.org_id,
                data_space_id=about.space,
                subject_type="skill",
                subject_id=about.skill_id,
                name=f"Ca kiểm thử · {about.name}",
                # BR-12's three passing cases are :publish's check, by scope; the set asks one.
                min_items=1,
                created_by=about.owner,
                updated_by=about.owner,
            )
            session.add(eval_set)
            await session.flush()
            reason = (
                f"{NO_SET}; đã tạo bộ “{eval_set.name}”, hãy thêm ca kiểm thử rồi kiểm định lại."
            )
            count = 0
        else:
            eval_set = found
            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ev.EvalItem)
                    .where(ev.EvalItem.eval_set_id == eval_set.id, ev.EvalItem.status == "active")
                )
                or 0
            )
            if count == 0:
                reason = "Bộ đánh giá của kỹ năng chưa có ca kiểm thử nào."
            elif about.space is None:
                # A skill run belongs to a Data Space (skill_run.data_space_id).
                reason = "Kỹ năng không thuộc Data Space nào nên chưa chạy được ca kiểm thử."
            else:
                reason = skill_runs.unrunnable(about.category, about.steps)
        baseline = await session.scalar(
            select(ev.EvalRun.id)
            .where(ev.EvalRun.eval_set_id == eval_set.id, ev.EvalRun.status == "completed")
            .order_by(ev.EvalRun.started_at.desc(), ev.EvalRun.id)
            .limit(1)
        )
        session.add(
            ev.EvalRun(
                id=run_id,
                org_id=event.org_id,
                eval_set_id=eval_set.id,
                subject_version=f"skill {about.skill_id} v{about.number}",
                trigger="manual",
                trigger_ref=about.id,
                item_count=count,
                baseline_run_id=baseline,
                status="running" if reason is None else "failed",
                finished_at=None if reason is None else _now(),
            )
        )
    return reason


async def _end_failed(org_id: uuid.UUID, owner: uuid.UUID, run_id: uuid.UUID) -> None:
    async with scoped_session(EVAL_ROLE, org_id, owner) as session:
        run = await session.get(ev.EvalRun, run_id, with_for_update=True)
        if run is not None and run.status == "running":
            run.status, run.finished_at = "failed", _now()


def _params(item: ev.EvalItem) -> dict[str, Any]:
    raw = item.input if isinstance(item.input, dict) else {}
    params = raw.get("params", raw)
    return dict(params) if isinstance(params, dict) else {}


async def _case(event: Event, run_id: uuid.UUID, about: _Version, item: ev.EvalItem) -> Any:
    """Runs one case as a skill run of the version and returns the finished run."""
    case_id = uuid7()
    assert about.space is not None  # _made fails a run without one
    async with scoped_session(SKILL_ROLE, event.org_id) as session:
        session.add(
            skill.SkillRun(
                id=case_id,
                org_id=event.org_id,
                skill_version_id=about.id,
                data_space_id=about.space,
                caller_kind="eval",
                caller_ref=run_id,
                principal_id=about.owner,
                params=_params(item),
                as_of=item.as_of,
            )
        )
    await skill_runs.run_skill(
        Event(
            uuid7(),
            event.org_id,
            skill_runs.EVENT_TYPE,
            "skill_run",
            case_id,
            {"skill_run_id": str(case_id)},
        )
    )
    async with scoped_session(SKILL_ROLE, event.org_id) as session:
        return await session.get(skill.SkillRun, case_id)


async def _score(event: Event, run_id: uuid.UUID, about: _Version) -> None:
    async with scoped_session(EVAL_ROLE, event.org_id, about.owner) as session:
        run = await session.get(ev.EvalRun, run_id)
        assert run is not None
        items = list(
            (
                await session.scalars(
                    select(ev.EvalItem)
                    .where(
                        ev.EvalItem.eval_set_id == run.eval_set_id, ev.EvalItem.status == "active"
                    )
                    .order_by(ev.EvalItem.id)
                )
            ).all()
        )
        baseline: dict[uuid.UUID, bool] = {}
        if run.baseline_run_id is not None:
            baseline = {
                item_id: bool(ok)
                for item_id, ok in (
                    await session.execute(
                        select(ev.EvalResult.eval_item_id, ev.EvalResult.passed).where(
                            ev.EvalResult.eval_run_id == run.baseline_run_id
                        )
                    )
                ).all()
            }
    # Each case is its own skill run, outside the eval transaction: a case may take minutes.
    scored = []
    for item in items:
        case = await _case(event, run_id, about, item)
        scored.append((item, score(item, case.status, case.result, case.error, case.id)))

    async with scoped_session(EVAL_ROLE, event.org_id, about.owner) as session:
        run = await session.get(ev.EvalRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return
        passed = regressions = 0
        for item, result in scored:
            changed = item.id in baseline and baseline[item.id] != result.passed
            regressions += int(changed and baseline[item.id] and not result.passed)
            passed += int(result.passed)
            # The figures were computed with the owner's rights: the api shows them to the owner
            # alone (eval routes._result_view), everyone else reads the scores and the cause.
            result.actual["answered_as"] = str(about.owner)
            session.add(
                ev.EvalResult(
                    id=uuid7(),
                    org_id=event.org_id,
                    eval_run_id=run_id,
                    eval_item_id=item.id,
                    understanding_ok=result.understanding_ok,
                    value_ok=result.value_ok,
                    behavior_ok=result.behavior_ok,
                    passed=result.passed,
                    actual=result.actual,
                    cause=result.cause,
                    changed_from_baseline=changed,
                )
            )
        run.item_count, run.passed, run.failed = len(scored), passed, len(scored) - passed
        run.correct_refusals = sum(
            1 for item, r in scored if r.passed and _wants_refusal(item.expected)
        )
        run.pass_rate = decimal.Decimal(passed) / len(scored) if scored else decimal.Decimal(0)
        run.regressions, run.status, run.finished_at = regressions, "completed", _now()


@dataclasses.dataclass
class Scored:
    understanding_ok: bool | None
    value_ok: bool | None
    behavior_ok: bool | None
    passed: bool
    actual: dict[str, Any]
    cause: str | None


def _wants_refusal(expected: Any) -> bool:
    return isinstance(expected, dict) and expected.get("behavior") == "refuse"


def _number(value: Any) -> decimal.Decimal | None:
    try:
        return decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        return None


def _close(actual: Any, expected: Any, tolerance: Any) -> bool:
    got, want = _number(actual), _number(expected)
    if got is None or want is None:
        return False
    allowed = (
        abs(decimal.Decimal(str(tolerance)))
        if tolerance is not None
        else abs(want) * RELATIVE_TOLERANCE
    )
    return abs(got - want) <= allowed


def score(
    item: ev.EvalItem,
    status: str,
    result: Any,
    error: str | None,
    case_id: uuid.UUID,
) -> Scored:
    """One case against what its item expects. The cause names which part missed, never a value
    (anyone who may read the run reads the cause)."""
    expected = item.expected if isinstance(item.expected, dict) else {}
    actual: dict[str, Any] = {"skill_run_id": str(case_id), "status": status}
    if _wants_refusal(expected):
        ok = status == "failed"
        actual["error"] = error
        return Scored(None, None, ok, ok, actual, None if ok else "expected to refuse, ran instead")
    if status != "succeeded" or not isinstance(result, dict):
        # Why it failed may name what the parameters asked for: it stays in `actual`, which the
        # api shows to the owner alone.
        actual["error"] = error
        return Scored(False, False, False, False, actual, f"the skill run {status}")
    raw_figures = result.get("figures")
    figures: dict[str, Any] = raw_figures if isinstance(raw_figures, dict) else {}
    actual |= {"dimension": result.get("dimension"), "figures": figures}
    tolerance = item.tolerance if item.tolerance is not None else expected.get("tolerance")
    checks = 0
    understanding_ok: bool | None = None
    if expected.get("dimension") is not None:
        checks += 1
        understanding_ok = result.get("dimension") == expected["dimension"]
    value_ok: bool | None = None
    wanted_figures = expected.get("figures")
    wanted_members = expected.get("contributions")
    if isinstance(wanted_figures, dict) or isinstance(wanted_members, dict):
        checks += 1
        value_ok = all(
            _close(figures.get(name), value, tolerance)
            for name, value in (wanted_figures or {}).items()
        )
        members = {
            str(c.get("member")): c.get("value")
            for c in figures.get("contributions") or []
            if isinstance(c, dict)
        }
        value_ok = value_ok and all(
            _close(members.get(str(member)), value, tolerance)
            for member, value in (wanted_members or {}).items()
        )
    behavior_ok: bool | None = None
    contains = expected.get("text_contains")
    if contains:
        checks += 1
        summary = str(result.get("summary") or "")
        behavior_ok = all(str(part) in summary for part in contains)
    if checks == 0:
        # A case that expects nothing proves nothing: it never passes a version.
        return Scored(None, None, None, False, actual, "the item states no expected result")
    missed = [
        name
        for name, ok in (
            ("dimension", understanding_ok),
            ("value", value_ok),
            ("summary", behavior_ok),
        )
        if ok is False
    ]
    passed = not missed
    cause = None if passed else f"the result's {', '.join(missed)} is not what the item expects"
    return Scored(understanding_ok, value_ok, behavior_ok, passed, actual, cause)


async def _gate(event: Event, run_id: uuid.UUID, about: _Version, reason: str | None) -> None:
    async with scoped_session(EVAL_ROLE, event.org_id, about.owner) as session:
        found = await session.execute(
            select(ev.EvalRun, ev.EvalSet)
            .join(ev.EvalSet, ev.EvalSet.id == ev.EvalRun.eval_set_id)
            .where(ev.EvalRun.id == run_id)
        )
        run, eval_set = found.one()
        rate = float(run.pass_rate or 0)
        at_the_bar = (
            run.status == "completed"
            and run.item_count >= eval_set.min_items
            and rate >= float(eval_set.pass_threshold)
        )
        why = (
            f"{run.passed}/{run.item_count} ca kiểm thử đạt ({rate:.1%}); cần ít nhất "
            f"{eval_set.min_items} ca và {float(eval_set.pass_threshold):.0%}."
            if run.status == "completed"
            else reason or "Lần kiểm định không chạy xong."
        )

    async with scoped_session(SKILL_ROLE, event.org_id) as session:
        version = await session.get(skill.SkillVersion, about.id, with_for_update=True)
        if version is None:
            return
        previous = version.status
        if at_the_bar and previous in ("testing", "needs_review"):
            if version.eval_run_id == run_id and previous == "testing":
                return  # a redelivery: applied already
            version.eval_run_id = run_id
            version.status = "testing" if previous == "testing" else "published"
        elif not at_the_bar and previous == "testing":
            version.status = "draft"
        elif not at_the_bar and previous == "needs_review":
            pass  # keeps running with its label (UC-03-04 1a); the owner is told below
        else:
            return
        session.add(
            skill.Outbox(
                id=uuid7(),
                org_id=event.org_id,
                aggregate_type="skill_version",
                aggregate_id=version.id,
                event_type="skill_version.tested",
                payload={
                    "skill_version_id": str(version.id),
                    "skill_id": str(about.skill_id),
                    "version": about.number,
                    "eval_run_id": str(run_id),
                    "passed": at_the_bar,
                    "previous_status": previous,
                    "status": version.status,
                },
            )
        )
    await _tell(
        event.org_id,
        about.owner,
        [about.owner],
        kind="task_done" if at_the_bar else "alert",
        title=(
            f"Phiên bản {about.number} của {about.name} đạt kiểm định"
            if at_the_bar
            else f"Phiên bản {about.number} của {about.name} chưa đạt kiểm định"
        ),
        body=why + _next_step(at_the_bar, previous),
        link=f"/agents/skills/{about.skill_id}",
        data_space_id=about.space,
        source_ref=run_id,
    )


def _next_step(at_the_bar: bool, previous: str) -> str:
    if previous == "needs_review":
        return " Phiên bản chạy lại bình thường." if at_the_bar else " Phiên bản vẫn chạy, có nhãn."
    return " Có thể gửi phát hành." if at_the_bar else " Phiên bản đã về nháp."


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: evaluate_skill}
