"""Data quality rules evaluated on the data (UC-06-07).

Each rule becomes one read-only query that counts its violations on the dataset's source, run
through the query executor like any other query (the Data Space boundary and the read-only
guard apply). The result is a quality_result row, and the dataset's quality_state follows its
rules: blocked while a high-severity rule fails (BR-116 holds such a table out of reports),
warning while another fails, ok when all pass.

- not_null: rows where the column is null; with params.where {column: [values]} only in those
  groups (a person's gender is required of individuals, not of companies);
- range: rows outside params.min / params.max;
- unique: rows beyond the first of each value;
- in_list: rows whose value is not one of params.values;
- sum_matches: 1 when the column's total is off params.expected by more than params.tolerance;
- freshness: 1 when the newest value of the column is older than params.max_age_hours;
- custom_sql: the rows params.sql returns (a query that selects the violating rows).

quality_rule.created runs the new rule at once (the dry run of UC-06-07 step 2), and
quality_rule.updated runs it again while it is active; the scheduler runs every active rule once a
day, and a pipeline that saves a table runs that table's rules. A high-severity rule that newly
fails asks the table's steward (or the Data Space's) to look, in Cần bạn (AC-06-07-03).
"""

from __future__ import annotations

import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, ingest, org
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query

logger = logging.getLogger(__name__)

ROLE = "svc_ingest"
CREATED = "quality_rule.created"
UPDATED = "quality_rule.updated"
# How a rule reads in the title of the request a failing high rule opens.
KIND_TEXT = {
    "not_null": "không rỗng",
    "unique": "không trùng",
    "range": "trong khoảng",
    "in_list": "thuộc danh sách",
    "sum_matches": "khớp tổng",
    "freshness": "đủ mới",
    "custom_sql": "SQL riêng",
}


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
        groups = []
        for column, values in dict(p.get("where") or {}).items():
            listed = ", ".join(_literal(str(v)) for v in values or [])
            if not listed:
                raise ValueError("a not_null rule's where names values")
            groups.append(f"CAST({_ident(str(column))} AS text) IN ({listed})")
        scope = "".join(f" AND {g}" for g in groups)
        return f"SELECT count(*) AS n FROM {table} WHERE {col} IS NULL{scope}"
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
) -> bool | None:
    """Run one rule; True passed, False failed (or could not run), None when it is gone.

    A rule runs with its own author's rights, the last person who wrote it, whoever triggered
    the check: a custom_sql rule reads what its author may read, never what the person running
    a pipeline or the scheduler could. A rule that cannot run is recorded as failed with why
    (fail closed: a high-severity rule that cannot be checked blocks its table)."""
    from services.worker_agent.questions import secrets

    async with scoped_session(ROLE, org_id) as session:
        rule = await session.get(ingest.QualityRule, rule_id)
        if rule is None or rule.status != "active":
            return None
        # Who wrote the rule is not recorded apart from who last touched it (an admin enabling
        # it is updated_by too): it runs under both, and passes only if both may run it.
        principals = list(dict.fromkeys(p for p in (rule.created_by, rule.updated_by) if p))
    if not principals:
        return None
    author = principals[0]
    async with scoped_session("svc_catalog", org_id, author) as session:
        dataset = await session.get(catalog.Dataset, rule.dataset_id)
        source = await _source(session, dataset, org_id) if dataset else None
    if dataset is None:
        return None
    failure: str | None = None  # a fixed category, stored; the detail goes to the log only
    detail: str | None = None
    violations = 0
    query_run: uuid.UUID | None = None
    if source is None:
        failure = "source unavailable"
    else:
        table = f"{_ident(dataset.schema_name)}.{_ident(dataset.table_name)}"
        try:
            sql = violation_sql(rule, table)
        except (KeyError, ValueError) as exc:
            failure, detail = "rule incomplete", str(exc)
        else:
            for principal in principals:
                outcome = await run_query(
                    org_id=org_id,
                    data_space_id=dataset.data_space_id,
                    principal_id=principal,
                    caller_kind="ad_hoc",
                    caller_id=rule_id,
                    source=source,
                    sql=sql,
                    secrets=secrets(),
                    limit=1,
                )
                if not outcome.ok or not outcome.rows:
                    failure, detail = "query did not run", outcome.error or outcome.status
                    break
                if principal == author:
                    query_run = outcome.query_run_id
                    violations = int(outcome.rows[0][0] or 0)
    passed = failure is None and violations == 0
    steward = dataset.steward_id
    if not passed and rule.severity == "high" and steward is None:
        async with scoped_session("svc_catalog", org_id, author) as session:
            steward = await session.scalar(
                select(org.DataSpace.steward_id).where(org.DataSpace.id == dataset.data_space_id)
            )
    async with scoped_session(ROLE, org_id, author) as session:
        before = await session.scalar(
            select(ingest.QualityResult.passed)
            .where(ingest.QualityResult.rule_id == rule_id)
            .order_by(ingest.QualityResult.checked_at.desc())
            .limit(1)
        )
        session.add(
            ingest.QualityResult(
                id=uuid7(),
                org_id=org_id,
                rule_id=rule_id,
                run_kind=run_kind,
                run_ref=run_ref,
                passed=passed,
                violations=violations,
                sample_ref=f"error: {failure}" if failure else str(query_run),
            )
        )
        if not passed and rule.severity == "high" and before is not False and steward:
            # A high rule that starts failing asks the table's steward (or the Data Space's) in
            # Cần bạn, once: not again on every check that still fails (AC-06-07-03).
            name = dataset.display_name or dataset.table_name
            session.add(
                ingest.Outbox(
                    id=uuid7(),
                    org_id=org_id,
                    aggregate_type="quality_rule",
                    aggregate_id=rule.id,
                    event_type="hitl.requested",
                    payload={
                        "hitl_request_id": str(uuid7()),
                        "data_space_id": str(dataset.data_space_id),
                        "kind": "data_review",
                        "risk": "high",
                        "title": (
                            f"Bảng {name} bị chặn báo cáo: quy tắc {rule.column_name or ''} "
                            f"{KIND_TEXT.get(rule.kind, rule.kind)} vi phạm {violations} dòng"
                            if failure is None
                            else f"Bảng {name} bị chặn báo cáo: quy tắc không kiểm được ({failure})"
                        ),
                        "context": {
                            "dataset_id": str(rule.dataset_id),
                            "quality_rule_id": str(rule.id),
                            "violations": violations,
                        },
                        "options": [],
                        "source_service": "ingest",
                        "source_ref": str(rule.id),
                        "created_by": str(author),
                        "assignee_id": str(steward),
                        "assignee_group": None,
                        "assignee_capability": None,
                        "approval_steps": [],
                        "excluded_approver_ids": [],
                        "due_at": None,
                        "parent_run_label": None,
                    },
                )
            )
    if failure:
        logger.info("worker-agent | quality rule %s: %s (%s)", rule_id, failure, detail)
    await refresh_state(org_id, dataset.id, author)
    return passed


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
        unchecked = 0
        for rule in rules:
            latest = await session.scalar(
                select(ingest.QualityResult.passed)
                .where(ingest.QualityResult.rule_id == rule.id)
                .order_by(ingest.QualityResult.checked_at.desc())
                .limit(1)
            )
            if latest is None:
                unchecked += 1
            elif not latest:
                failing.append(rule.severity)
    # A table is ok only when every active rule has passed its last check.
    state = (
        "blocked"
        if "high" in failing
        else "warning"
        if failing
        else "unknown"
        if unchecked or not rules
        else "ok"
    )
    async with scoped_session("svc_catalog", org_id, actor) as session:
        await session.execute(
            update(catalog.Dataset)
            .where(catalog.Dataset.id == dataset_id)
            .values(quality_state=state)
        )
    return state


async def rule_updated(event: Event) -> None:
    """An edited rule is checked again while active; a disabled one is left as it is."""
    await rule_created(event)


async def rule_created(event: Event) -> None:
    async with scoped_session(ROLE, event.org_id) as session:
        rule = await session.get(ingest.QualityRule, event.aggregate_id)
        if rule is None:
            return
    await evaluate(event.org_id, rule.id, run_kind="manual", run_ref=None)


async def evaluate_dataset(
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    *,
    run_kind: str,
    run_ref: uuid.UUID | None,
) -> int:
    async with scoped_session(ROLE, org_id) as session:
        ids = (
            await session.scalars(
                select(ingest.QualityRule.id).where(
                    ingest.QualityRule.dataset_id == dataset_id,
                    ingest.QualityRule.status == "active",
                )
            )
        ).all()
    for rule_id in ids:
        await evaluate(org_id, rule_id, run_kind=run_kind, run_ref=run_ref)
    return len(ids)


HANDLERS: dict[str, Handler] = {CREATED: rule_created, UPDATED: rule_updated}
