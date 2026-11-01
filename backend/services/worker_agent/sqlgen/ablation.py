"""The ablation of the SQL expert (R1-09, 01 §3.4): what each way of writing earns on a Data
Space's eval set, measured with the model the cluster serves.

Every number item of the set (expected kind data with one value) goes through the exploratory
path as a question would — linked to the Data Space's tables, written, checked, repaired once,
run read-only in place and voted on — once per variant: all three ways (the production setting)
and each way alone. A variant scores an item correct when the vote answers (answer or review)
and its one figure is within 0.5 % of the expected value. The report gives, per variant, how
many it answered, how many it got right, its precision when it answered, and the mean confidence
of its right and wrong answers (the vote's share, the confidence meter of R1-14).

It writes nothing but the query runs (caller_kind eval) and reads as the person named, so it sees
what they may. In the worker pod:
    make sqlgen-ablation ABLATION_ORG=<org id> ABLATION_SPACE=DS-BH ABLATION_AS=<username> \\
         ABLATION_SET="Câu hỏi pilot Bán hàng"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, org
from libs.dataagent_db.models import eval as ev
from libs.sql_guard import dialect_of
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.sqlgen.answer import QUERYABLE, _keep_one_source, figures_of
from services.worker_agent.sqlgen.generate import METHODS
from services.worker_agent.sqlgen.run import explore
from services.worker_agent.sqlgen.schema import link
from services.worker_agent.sqlgen.space import load_space
from services.worker_agent.sqlgen.vote import ANSWER_AT, CANDIDATES, REVIEW_AT

QA_ROLE = "svc_qa"
EVAL_ROLE = "svc_eval"
RELATIVE_TOLERANCE = 0.005
VARIANTS: dict[str, tuple[str, ...]] = {"all": METHODS, **{m: (m,) for m in METHODS}}


@dataclass
class Outcome:
    item_id: str
    question: str
    decision: str  # answer, review, ask, failed, unlinked
    confidence: float
    value: float | None
    expected: float
    correct: bool


@dataclass
class Summary:
    variant: str
    items: int
    answered: int
    correct: int
    accuracy: float
    precision: float
    confidence_right: float | None
    confidence_wrong: float | None
    outcomes: list[Outcome] = field(default_factory=list)


def decision_of(decision: str, confidence: float) -> str:
    """The vote's decision by its share, so a variant of fewer candidates is judged on the bar
    production uses (4 of 6 answer, 3 of 6 review): vote() counts candidates, and a variant of
    one way writes two, which could never reach four."""
    if decision == "failed":
        return decision
    if confidence >= ANSWER_AT / CANDIDATES:
        return "answer"
    return "review" if confidence >= REVIEW_AT / CANDIDATES else "ask"


def close(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) <= abs(expected) * RELATIVE_TOLERANCE


def summarize(variant: str, outcomes: Sequence[Outcome]) -> Summary:
    answered = [o for o in outcomes if o.decision in ("answer", "review")]
    right = [o for o in answered if o.correct]
    wrong = [o for o in answered if not o.correct]

    def mean(xs: list[Outcome]) -> float | None:
        return round(sum(o.confidence for o in xs) / len(xs), 4) if xs else None

    n = len(outcomes)
    return Summary(
        variant=variant,
        items=n,
        answered=len(answered),
        correct=len(right),
        accuracy=round(len(right) / n, 4) if n else 0.0,
        precision=round(len(right) / len(answered), 4) if answered else 0.0,
        confidence_right=mean(right),
        confidence_wrong=mean(wrong),
        outcomes=list(outcomes),
    )


async def _who(org_id: uuid.UUID, space_code: str, username: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with scoped_session(QA_ROLE, org_id) as session:
        space = await session.scalar(
            select(org.DataSpace.id).where(
                org.DataSpace.org_id == org_id, org.DataSpace.code == space_code
            )
        )
        user = await session.scalar(
            select(org.AppUser.id).where(
                org.AppUser.org_id == org_id, org.AppUser.username == username
            )
        )
    if space is None or user is None:
        raise SystemExit(f"no Data Space {space_code} or user {username} in {org_id}")
    return space, user


async def _items(
    org_id: uuid.UUID, user: uuid.UUID, space: uuid.UUID, name: str
) -> list[ev.EvalItem]:
    async with scoped_session(EVAL_ROLE, org_id, user) as session:
        found = await session.scalar(
            select(ev.EvalSet.id).where(ev.EvalSet.data_space_id == space, ev.EvalSet.name == name)
        )
        if found is None:
            raise SystemExit(f"no eval set {name!r} in the Data Space")
        return list(
            (
                await session.scalars(
                    select(ev.EvalItem)
                    .where(ev.EvalItem.eval_set_id == found, ev.EvalItem.status == "active")
                    .order_by(ev.EvalItem.id)
                )
            ).all()
        )


async def run(
    org_id: uuid.UUID,
    space_code: str,
    username: str,
    set_name: str,
    variants: Sequence[str],
    limit: int | None,
) -> list[Summary]:
    # The worker's own gateway, secrets and workspace source: the production path's.
    from services.worker_agent.questions import _internal_workspace_source, gateway, secrets

    model = gateway()
    if model is None:
        raise SystemExit("no model gateway configured (LITELLM_*)")
    space, user = await _who(org_id, space_code, username)
    items = [
        i
        for i in await _items(org_id, user, space, set_name)
        if isinstance(i.expected, dict)
        and i.expected.get("kind") == "data"
        and len(i.expected.get("values") or []) == 1
    ][:limit]
    reader = secrets()
    found: dict[str, list[Outcome]] = {v: [] for v in variants}
    for item in items:
        text = str((item.input or {}).get("question") or "")
        expected = float(item.expected["values"][0])
        async with scoped_session(QA_ROLE, org_id, user) as session:
            schema = await load_space(session, space, user)
            linked = link(text, schema.schema)
            source: catalog.Source | None = None
            if linked.tables:
                linked, src = _keep_one_source(linked, schema.source_of)
                source = (
                    _internal_workspace_source(space, org_id)
                    if src is None
                    else await session.get(catalog.Source, src)
                )
                if source is not None and src is not None:
                    session.expunge(source)
        if source is None or source.mode != "federated" or source.status not in QUERYABLE:
            for v in variants:
                found[v].append(Outcome(str(item.id), text, "unlinked", 0.0, None, expected, False))
            continue

        async def run_sql(
            sql: str, item_id: uuid.UUID = item.id, src: catalog.Source = source
        ) -> QueryOutcome:
            return await run_query(
                org_id=org_id,
                data_space_id=space,
                principal_id=user,
                caller_kind="eval",
                caller_id=item_id,
                source=src,
                sql=sql,
                secrets=reader,
            )

        for v in variants:
            e = await explore(
                model,
                text,
                linked,
                dialect_of(source.kind),
                schema.schema.examples,
                run_sql,
                methods=VARIANTS[v],
            )
            decided = decision_of(e.decision, e.confidence)
            figures = figures_of(e.columns, e.rows) if decided in ("answer", "review") else []
            value = (
                float(figures[0].value)
                if len(figures) == 1 and figures[0].value is not None
                else None
            )
            found[v].append(
                Outcome(
                    str(item.id),
                    text,
                    decided,
                    round(e.confidence, 4),
                    value,
                    expected,
                    decided in ("answer", "review") and close(value, expected),
                )
            )
    return [summarize(v, found[v]) for v in variants]


def _table(summaries: Sequence[Summary]) -> str:
    lines = ["variant    items  answered  correct  accuracy  precision  conf(right)  conf(wrong)"]
    for s in summaries:
        lines.append(
            f"{s.variant:<10} {s.items:>5}  {s.answered:>8}  {s.correct:>7}  {s.accuracy:>8.1%}"
            f"  {s.precision:>9.1%}  {s.confidence_right if s.confidence_right is not None else '-':>11}"
            f"  {s.confidence_wrong if s.confidence_wrong is not None else '-':>11}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True, type=uuid.UUID)
    parser.add_argument("--space", required=True)
    parser.add_argument("--as", dest="username", required=True)
    parser.add_argument("--set", dest="set_name", required=True)
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="the full report, every item, as JSON")
    args = parser.parse_args(argv)
    variants = [v for v in args.variants.split(",") if v]
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        parser.error(f"unknown variants {sorted(unknown)}; known: {', '.join(VARIANTS)}")
    summaries = asyncio.run(
        run(args.org, args.space, args.username, args.set_name, variants, args.limit)
    )
    if args.json:
        json.dump([asdict(s) for s in summaries], sys.stdout, ensure_ascii=False, indent=1)
    else:
        print(_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
