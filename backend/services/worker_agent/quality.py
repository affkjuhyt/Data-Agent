"""Data quality rules evaluated on the data (UC-06-07).

Each rule becomes one read-only query that counts its violations on the dataset's source, run
through the query executor like any other query (the Data Space boundary and the read-only
guard apply). The result is a quality_result row, and the dataset's quality_state follows its
rules: blocked while a high-severity rule fails (BR-116 holds such a table out of reports),
warning while another fails, ok when all pass.

- not_null: rows where the column is null;
- range: rows outside params.min / params.max;
- unique: rows beyond the first of each value;
- in_list: rows whose value is not one of params.values;
- sum_matches: 1 when the column's total is off params.expected by more than params.tolerance;
- freshness: 1 when the newest value of the column is older than params.max_age_hours;
- custom_sql: the rows params.sql returns (a query that selects the violating rows).

quality_rule.created runs the new rule at once (the dry run of UC-06-07 step 2); the scheduler
runs every active rule once a day, and a pipeline that saves a table runs that table's rules.
"""

from __future__ import annotations

import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, ingest
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query

logger = logging.getLogger(__name__)

ROLE = "svc_ingest"
CREATED = "quality_rule.created"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float | decimal.Decimal):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def violation_sql(rule: ingest.QualityRule, table: str) -> str:
    """The query counting the rule's violations, as one row with column `n`."""
    p = dict(rule.params or {})
    col = _ident(rule.column_name) if rule.column_name else None
    if rule.kind == "custom_sql":
        return f"SELECT count(*) AS n FROM ({str(p['sql']).strip().rstrip(';')}) v"
    if col is None:
        raise ValueError(f"a {rule.kind} rule names a column")
    if rule.kind == "not_null":
        return f"SELECT count(*) AS n FROM {table} WHERE {col} IS NULL"
    if rule.kind == "range":
        bounds = []
        if p.get("min") is not None:
            bounds.append(f"{col} < {_literal(p['min'])}")
        if p.get("max") is not None:
            bounds.append(f"{col} > {_literal(p['max'])}")
        if not bounds:
            raise ValueError("a range rule needs min or max")
        return f"SELECT count(*) AS n FROM {table} WHERE {' OR '.join(bounds)}"
    if rule.kind == "unique":
        return f"SELECT count(*) - count(DISTINCT {col}) AS n FROM {table} WHERE {col} IS NOT NULL"
    if rule.kind == "in_list":
        # The column is compared as text, so the listed values are too (1 matches '1').
        values = ", ".join(_literal(str(v)) for v in p.get("values") or [])
        if not values:
            raise ValueError("an in_list rule needs values")
        return (
            f"SELECT count(*) AS n FROM {table} "
            f"WHERE {col} IS NOT NULL AND CAST({col} AS text) NOT IN ({values})"
        )
    if rule.kind == "sum_matches":
        tolerance = _literal(p.get("tolerance", 0))
        return (
            f"SELECT CASE WHEN abs(coalesce(sum({col}), 0) - {_literal(p['expected'])}) > {tolerance}"
            f" THEN 1 ELSE 0 END AS n FROM {table}"
        )
    if rule.kind == "freshness":
        hours = int(p.get("max_age_hours", 24))
        return (
            f"SELECT CASE WHEN max({col}) IS NULL OR max({col}) < now() - interval '{hours} hours'"
            f" THEN 1 ELSE 0 END AS n FROM {table}"
        )
    raise ValueError(f"unknown rule kind {rule.kind}")


async def _source(
    session: Any, dataset: catalog.Dataset, org_id: uuid.UUID
) -> catalog.Source | None:
    from services.worker_agent.questions import _internal_workspace_source

    if dataset.source_id is None:
        return _internal_workspace_source(dataset.data_space_id, org_id)
    found: catalog.Source | None = await session.get(catalog.Source, dataset.source_id)
    return found


async def evaluate(
    org_id: uuid.UUID,
    rule_id: uuid.UUID,
    *,
    run_kind: str,
    run_ref: uuid.UUID | None,
    actor: uuid.UUID,
) -> bool | None:
    """Run one rule; True passed, False failed, None when it could not run."""
    from services.worker_agent.questions import secrets

    async with scoped_session(ROLE, org_id, actor) as session:
        rule = await session.get(ingest.QualityRule, rule_id)
        if rule is None or rule.status == "disabled":
            return None
    async with scoped_session("svc_catalog", org_id, actor) as session:
        dataset = await session.get(catalog.Dataset, rule.dataset_id)
        source = await _source(session, dataset, org_id) if dataset else None
    if dataset is None or source is None:
        return None
    table = f"{_ident(dataset.schema_name)}.{_ident(dataset.table_name)}"
    try:
        sql = violation_sql(rule, table)
    except (KeyError, ValueError) as exc:
        logger.info("worker-agent | quality rule %s cannot run: %s", rule_id, exc)
        return None
    outcome = await run_query(
        org_id=org_id,
        data_space_id=dataset.data_space_id,
        principal_id=actor,
        caller_kind="ad_hoc",
        caller_id=rule_id,
        source=source,
        sql=sql,
        secrets=secrets(),
        limit=1,
    )
    if not outcome.ok or not outcome.rows:
        logger.info("worker-agent | quality rule %s failed to run: %s", rule_id, outcome.error)
        return None
    violations = int(outcome.rows[0][0] or 0)
    async with scoped_session(ROLE, org_id, actor) as session:
        session.add(
            ingest.QualityResult(
                id=uuid7(),
                org_id=org_id,
                rule_id=rule_id,
                run_kind=run_kind,
                run_ref=run_ref,
                passed=violations == 0,
                violations=violations,
                sample_ref=str(outcome.query_run_id),
            )
        )
    await refresh_state(org_id, dataset.id, actor)
    return violations == 0


async def refresh_state(org_id: uuid.UUID, dataset_id: uuid.UUID, actor: uuid.UUID) -> str:
    """The dataset's quality_state from the latest result of each of its active rules."""
    async with scoped_session(ROLE, org_id, actor) as session:
        rules = (
            await session.scalars(
                select(ingest.QualityRule).where(
                    ingest.QualityRule.dataset_id == dataset_id,
                    ingest.QualityRule.status == "active",
                )
            )
        ).all()
        failing: list[str] = []
        known = 0
        for rule in rules:
            latest = await session.scalar(
                select(ingest.QualityResult.passed)
                .where(ingest.QualityResult.rule_id == rule.id)
                .order_by(ingest.QualityResult.checked_at.desc())
                .limit(1)
            )
            if latest is None:
                continue
            known += 1
            if not latest:
                failing.append(rule.severity)
    state = (
        "blocked" if "high" in failing else "warning" if failing else "ok" if known else "unknown"
    )
    async with scoped_session("svc_catalog", org_id, actor) as session:
        await session.execute(
            update(catalog.Dataset)
            .where(catalog.Dataset.id == dataset_id)
            .values(quality_state=state)
        )
    return state


async def rule_created(event: Event) -> None:
    async with scoped_session(ROLE, event.org_id) as session:
        rule = await session.get(ingest.QualityRule, event.aggregate_id)
        if rule is None:
            return
        actor = rule.created_by or rule.updated_by
    if actor is not None:
        await evaluate(event.org_id, rule.id, run_kind="manual", run_ref=None, actor=actor)


async def evaluate_dataset(
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    *,
    run_kind: str,
    run_ref: uuid.UUID | None,
    actor: uuid.UUID,
) -> int:
    async with scoped_session(ROLE, org_id, actor) as session:
        ids = (
            await session.scalars(
                select(ingest.QualityRule.id).where(
                    ingest.QualityRule.dataset_id == dataset_id,
                    ingest.QualityRule.status == "active",
                )
            )
        ).all()
    for rule_id in ids:
        await evaluate(org_id, rule_id, run_kind=run_kind, run_ref=run_ref, actor=actor)
    return len(ids)


HANDLERS: dict[str, Handler] = {CREATED: rule_created}
