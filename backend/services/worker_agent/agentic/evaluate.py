"""What the dynamic agent earns on a Data Space's eval set, beside the governed path: each item is
asked as a question would be (POST /v1/questions does the same two inserts) and answered by the
production handler, once per engine (ASK_ENGINE), then scored by the eval runs' own scorer
(eval_runs._score), so a pass here is a pass there.

Per engine the report gives how many items passed, how many had the right values in any order
(the agent may sort a breakdown its own way), and how each was answered: by the agent itself, by
the agent handing over to the governed path, or by the governed path alone; with the steps, model
tokens and seconds each took.

It writes what asking writes: a conversation, a question, its answer and its run per item and
engine, as the person named. In the worker pod:
    make agent-eval AGENT_EVAL_ORG=<org id> AGENT_EVAL_AS=<username> \\
         AGENT_EVAL_ARGS="--limit 10 --engines agentic,governed"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import agent, qa
from libs.dataagent_db.models import eval as ev
from services.worker_agent import eval_runs
from services.worker_agent.events import Event
from services.worker_agent.sqlgen.ablation import _items, _who

QA_ROLE = "svc_qa"
AGENT_ROLE = "svc_agent"
ENGINES = ("agentic", "governed")


@dataclass
class Outcome:
    item_id: str
    question: str
    engine: str
    path: str  # agentic, handover, governed, none
    kind: str | None
    passed: bool
    values_any_order: bool | None
    cause: str | None
    steps: int
    tokens: int
    seconds: float
    answer: str = ""


@dataclass
class Summary:
    engine: str
    items: int
    passed: int
    values_any_order: int
    by_path: dict[str, int]
    mean_steps: float
    mean_tokens: float
    mean_seconds: float
    outcomes: list[Outcome] = field(default_factory=list)


def _any_order(item: ev.EvalItem, answer: qa.Answer | None) -> bool | None:
    expected = item.expected if isinstance(item.expected, dict) else {}
    wanted = expected.get("values")
    if wanted is None and "value" in expected:
        wanted = [expected["value"]]
    if wanted is None or answer is None:
        return None
    got = sorted(eval_runs._values(answer.figures))
    want = sorted(float(v) for v in wanted)
    return len(got) == len(want) and all(
        abs(a - e) <= abs(e) * eval_runs.RELATIVE_TOLERANCE for a, e in zip(got, want, strict=True)
    )


async def _trace(
    org_id: uuid.UUID, user: uuid.UUID, question_id: uuid.UUID
) -> tuple[int, int, bool]:
    """Steps (model calls left out), tokens, and whether the agent handed the turn over."""
    async with scoped_session(AGENT_ROLE, org_id, user) as session:
        run = await session.scalar(
            select(agent.AgentRun)
            .where(agent.AgentRun.trigger_ref == f"question:{question_id}")
            .order_by(agent.AgentRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return 0, 0, False
        names = list(
            (
                await session.scalars(
                    select(agent.AgentStep.name).where(agent.AgentStep.run_id == run.id)
                )
            ).all()
        )
    return sum(1 for n in names if n != "model_call"), run.tokens or 0, "handover" in names


async def _ask(
    org_id: uuid.UUID, space: uuid.UUID, user: uuid.UUID, item: ev.EvalItem, engine: str
) -> Outcome:
    from services.worker_agent.questions import answer_question

    os.environ["ASK_ENGINE"] = engine
    text = eval_runs._question_text(item)
    question_id, conversation_id = uuid7(), uuid7()
    async with scoped_session(QA_ROLE, org_id, user) as session:
        session.add(
            qa.Conversation(
                id=conversation_id,
                org_id=org_id,
                data_space_id=space,
                user_id=user,
                channel="api",
                created_by=user,
                updated_by=user,
            )
        )
        session.add(
            qa.Question(
                id=question_id,
                org_id=org_id,
                conversation_id=conversation_id,
                seq=1,
                text=text,
                as_of=item.as_of,
                received_at=func.now(),
            )
        )
    started = time.monotonic()
    await answer_question(
        Event(
            uuid7(),
            org_id,
            "question.received",
            "question",
            question_id,
            {"question_id": str(question_id), "user_id": str(user)},
        )
    )
    seconds = time.monotonic() - started
    async with scoped_session(QA_ROLE, org_id, user) as session:
        question = await session.get(qa.Question, question_id)
        answer = await session.scalar(select(qa.Answer).where(qa.Answer.question_id == question_id))
        plan = await session.get(qa.Plan, answer.plan_id) if answer is not None else None
    plan_steps = plan.steps if plan is not None else None
    agentic = isinstance(plan_steps, dict) and plan_steps.get("engine") == "agentic"
    # The metric the answer was read as, which an item may expect: the agent's first certified
    # dataset, or the governed plan's semantic step (eval_runs reads that one).
    understood = (
        next(
            (
                d.get("metric")
                for d in (plan_steps.get("datasets") or {}).values()  # type: ignore[union-attr]
                if isinstance(d, dict) and d.get("metric")
            ),
            None,
        )
        if agentic
        else await eval_runs._understood_metric(org_id, user, question_id)
    )
    _, _, _, passed, _, cause = eval_runs._score(item, answer, question, understood)
    steps, tokens, handed_over = await _trace(org_id, user, question_id)
    path = (
        "none"
        if answer is None
        else "agentic"
        if agentic
        else "handover"
        if handed_over
        else "governed"
    )
    return Outcome(
        str(item.id),
        text,
        engine,
        path,
        answer.kind if answer else None,
        passed,
        _any_order(item, answer),
        cause,
        steps,
        tokens,
        round(seconds, 1),
        (answer.text or "")[:160] if answer else "",
    )


def summarize(engine: str, outcomes: Sequence[Outcome]) -> Summary:
    n = len(outcomes) or 1
    paths: dict[str, int] = {}
    for o in outcomes:
        paths[o.path] = paths.get(o.path, 0) + 1
    return Summary(
        engine=engine,
        items=len(outcomes),
        passed=sum(o.passed for o in outcomes),
        values_any_order=sum(bool(o.values_any_order) for o in outcomes),
        by_path=paths,
        mean_steps=round(sum(o.steps for o in outcomes) / n, 1),
        mean_tokens=round(sum(o.tokens for o in outcomes) / n),
        mean_seconds=round(sum(o.seconds for o in outcomes) / n, 1),
        outcomes=list(outcomes),
    )


async def run(
    org_id: uuid.UUID,
    space_code: str,
    username: str,
    set_name: str,
    engines: Sequence[str],
    limit: int | None,
    offset: int,
) -> list[Summary]:
    space, user = await _who(org_id, space_code, username)
    items = (await _items(org_id, user, space, set_name))[offset:][:limit]
    found: dict[str, list[Outcome]] = {e: [] for e in engines}
    for item in items:
        for engine in engines:
            outcome = await _ask(org_id, space, user, item, engine)
            found[engine].append(outcome)
            # One line per answer as it comes, so a long run shows its progress.
            print(
                f"[{engine}] {'PASS' if outcome.passed else 'fail'} {outcome.path:<8} "
                f"{outcome.steps:>2} steps {outcome.seconds:>5}s  {outcome.question[:70]}",
                file=sys.stderr,
                flush=True,
            )
    return [summarize(e, found[e]) for e in engines]


def _table(summaries: Sequence[Summary]) -> str:
    lines = [
        "engine     items  passed  any-order  agentic  handover  governed  steps  tokens  seconds"
    ]
    for s in summaries:
        lines.append(
            f"{s.engine:<10} {s.items:>5}  {s.passed:>6}  {s.values_any_order:>9}"
            f"  {s.by_path.get('agentic', 0):>7}  {s.by_path.get('handover', 0):>8}"
            f"  {s.by_path.get('governed', 0):>8}  {s.mean_steps:>5}  {s.mean_tokens:>6}"
            f"  {s.mean_seconds:>7}"
        )
    for s in summaries:
        lines.append(f"\n{s.engine}: what did not pass")
        lines += [
            f"  {o.path:<8} {o.kind or '-':<13} {o.question[:60]} → {o.answer[:90]}"
            for o in s.outcomes
            if not o.passed
        ]
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--org", required=True, type=uuid.UUID)
    parser.add_argument("--space", default="DS-AW")
    parser.add_argument("--as", dest="username", required=True)
    parser.add_argument("--set", dest="set_name", default="Câu hỏi AdventureWorks")
    parser.add_argument("--engines", default="agentic")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    engines = [e for e in args.engines.split(",") if e in ENGINES]
    if not engines:
        raise SystemExit(f"--engines takes {', '.join(ENGINES)}")
    summaries = asyncio.run(
        run(args.org, args.space, args.username, args.set_name, engines, args.limit, args.offset)
    )
    if args.json:
        print(json.dumps([asdict(s) for s in summaries], ensure_ascii=False, indent=2))
    else:
        print(_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
