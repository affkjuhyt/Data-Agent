"""Replay historical query runs for a pending access-policy backtest (AC-11-02-02)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select, text, update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, governance, notify, org
from libs.dataagent_db.models import query as q
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import (
    INTERNAL_WORKSPACE,
    _hash,
    driver_for,
)
from services.worker_agent.query.executor import (
    ROLE as QUERY_ROLE,
)
from services.worker_agent.query.guard import SqlRejected, check_read_only, dialect_of, tables_of
from services.worker_agent.query.scope import apply_output_policies, rewrite_access_policies
from services.worker_agent.questions import secrets

logger = logging.getLogger(__name__)

EVENT_TYPE = "policy.backtest_requested"
IMPACT_WARNING = "policy.impact_warning"
MAX_RUNS = 2_000


def _applies(
    policy: governance.AccessPolicy,
    principal_id: uuid.UUID,
    role_ids: set[str],
    group_ids: set[str],
) -> bool:
    raw = policy.applies_to if isinstance(policy.applies_to, dict) else {}
    users = {str(value) for value in raw.get("user_ids", [])}
    roles = {str(value) for value in raw.get("role_ids", [])}
    groups = {str(value) for value in raw.get("group_ids", [])}
    return (
        not (users or roles or groups)
        or str(principal_id) in users
        or bool(roles & role_ids)
        or bool(groups & group_ids)
    )


def _mentions_table(sql: str, dialect: str, table_name: str) -> bool:
    try:
        names = {name.lower() for _, name in tables_of(check_read_only(sql, dialect), dialect)}
    except SqlRejected:
        return False
    return table_name.lower() in names


async def _principal_groups(session: Any, principal_id: uuid.UUID) -> tuple[set[str], set[str]]:
    role_ids = {
        str(value)
        for value in (
            await session.scalars(
                select(org.UserRole.role_id).where(org.UserRole.user_id == principal_id)
            )
        ).all()
    }
    group_ids = {
        str(value)
        for value in (
            await session.scalars(
                select(org.UserGroupMember.group_id).where(
                    org.UserGroupMember.user_id == principal_id
                )
            )
        ).all()
    }
    return role_ids, group_ids


async def _candidate_hash(
    session: Any,
    run: q.QueryRun,
    source: catalog.Source,
    dataset: catalog.Dataset,
    policy: governance.AccessPolicy,
    user: org.AppUser,
) -> str:
    dialect = dialect_of(source.kind)
    sql = check_read_only(run.sql_generated, dialect).sql(dialect=dialect)
    if policy.kind == "row":
        sql = rewrite_access_policies(
            sql,
            dialect=dialect,
            policies=[policy],
            user=user,
            datasets=[dataset],
            source=source,
        )
    if source.secret_ref == INTERNAL_WORKSPACE:
        await session.execute(text("SET LOCAL search_path TO workspace, pg_catalog"))
        result = await session.execute(text(sql))
        columns = [{"name": name, "type": "unknown"} for name in tuple(result.keys())]
        rows = [list(row) for row in result.fetchall()]
    else:
        credentials = await secrets().read(run.org_id, source.secret_ref)
        columns, rows = await asyncio.wait_for(
            driver_for(source.kind)(
                source,
                credentials.user,
                credentials.password,
                sql,
                int(source.query_timeout_s or 120),
            ),
            timeout=int(source.query_timeout_s or 120) + 5,
        )
    if policy.kind in {"column", "mask"}:
        columns, rows = apply_output_policies(columns, rows, [policy])
    return _hash(columns, rows)


async def _backtest(event: Event) -> None:
    payload = event.payload
    backtest_id = uuid.UUID(str(payload["backtest_id"]))
    requester = payload.get("requested_by")
    if requester is None:
        logger.error("policy backtest %s has no requester", backtest_id)
        return
    requester_id = uuid.UUID(str(requester))
    async with scoped_session(QUERY_ROLE, event.org_id, requester_id) as session:
        backtest = await session.scalar(
            select(governance.PolicyBacktest).where(
                governance.PolicyBacktest.id == backtest_id,
                governance.PolicyBacktest.org_id == event.org_id,
            )
        )
        if backtest is None or backtest.policy_kind != "access":
            return
        policy = await session.scalar(
            select(governance.AccessPolicy).where(
                governance.AccessPolicy.id == backtest.policy_id,
                governance.AccessPolicy.org_id == event.org_id,
            )
        )
        if policy is None:
            return
        runs = (
            await session.scalars(
                select(q.QueryRun)
                .where(
                    q.QueryRun.org_id == event.org_id,
                    q.QueryRun.source_id.is_not(None),
                    q.QueryRun.started_at >= backtest.window_from,
                    q.QueryRun.started_at <= backtest.window_to,
                    q.QueryRun.sql_generated != "",
                )
                .order_by(q.QueryRun.started_at)
                .limit(MAX_RUNS)
            )
        ).all()
        matched = changed = blocked = 0
        affected: set[uuid.UUID] = set()
        for run in runs:
            source = await session.get(catalog.Source, run.source_id)
            dataset = await session.get(catalog.Dataset, policy.dataset_id)
            if (
                source is None
                or dataset is None
                or dataset.source_id != run.source_id
                or dataset.data_space_id != run.data_space_id
                or not _mentions_table(
                    run.sql_generated, dialect_of(source.kind), dataset.table_name
                )
            ):
                continue
            role_ids, group_ids = await _principal_groups(session, run.principal_id)
            if not _applies(policy, run.principal_id, role_ids, group_ids):
                continue
            matched += 1
            if run.caller_kind == "agent_run":
                affected.add(run.caller_id)
            user = await session.get(org.AppUser, run.principal_id)
            if user is None:
                blocked += 1
                continue
            try:
                candidate = await _candidate_hash(session, run, source, dataset, policy, user)
                if candidate != run.result_hash:
                    changed += 1
            except Exception:
                logger.exception("policy backtest replay failed for query run %s", run.id)
                blocked += 1
        async with scoped_session("svc_governance", event.org_id, requester_id) as write_session:
            await write_session.execute(
                update(governance.PolicyBacktest)
                .where(governance.PolicyBacktest.id == backtest.id)
                .values(
                    matched_count=matched,
                    auto_handled_count=matched - blocked,
                    blocked_count=blocked,
                    changed_result_count=changed,
                    affected_agent_ids=list(affected),
                )
            )


async def _impact_warning(event: Event) -> None:
    """Create one safe in-app warning per affected agent owner; retries are idempotent."""
    affected = [uuid.UUID(str(value)) for value in event.payload.get("affected_agent_ids") or []]
    if not affected:
        return
    policy_id = event.payload.get("policy_id", str(event.aggregate_id))
    changed = int(event.payload.get("changed_result_count", 0))
    async with scoped_session("svc_notify", event.org_id) as session:
        for user_id in affected:
            exists = await session.scalar(
                select(notify.Notification.id).where(
                    notify.Notification.source_ref == event.event_id,
                    notify.Notification.user_id == user_id,
                    notify.Notification.kind == "alert",
                )
            )
            if exists:
                continue
            session.add(
                notify.Notification(
                    id=uuid.uuid4(),
                    org_id=event.org_id,
                    user_id=user_id,
                    data_space_id=event.payload.get("data_space_id"),
                    kind="alert",
                    title="Quyền dữ liệu sắp thay đổi",
                    body=f"Policy sẽ làm thay đổi {changed} lượt chạy lịch sử của bạn.",
                    link=f"/admin/access/policies/{policy_id}",
                    source_ref=event.event_id,
                    priority="high",
                )
            )


HANDLERS: dict[str, Handler] = {
    EVENT_TYPE: _backtest,
    IMPACT_WARNING: _impact_warning,
}
