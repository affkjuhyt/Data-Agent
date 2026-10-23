"""Scheduled reports (UC-08-03, UC-02-04): the `report_run.requested` handler.

A run comes from POST /v1/reports/{id}:run-now or from the scheduler (scheduler.py) when the
schedule's cron fires. It renders the workspace's tiles once per permission group (BR-34: the
North group's file holds no South rows), runs the pre-send checks when the schedule asks for
them, stores each rendering in MinIO and tells every recipient in the app.

Rights. A recipient row carries the hash of its permission set taken when the schedule was saved
(rules.permission_group in the api). Each group is rendered with one member's rights, its
representative, and every other member must run each tile's query as the very same SQL
(executor.bound_sql); one who would not, because their scope or a policy differs since, is left
out of that rendering, named in the run's checks, and the owner is told. Nobody receives figures
computed with someone else's rights.

What is not done yet, and fails or is reported rather than pretended:
- `kind=digest` (UC-08-04: key metrics, alerts, the "Cần bạn" items) ends `failed`, saying so;
- the queries run on the source as it is now: a tile reads live data, so `as_of` (from the
  schedule's as_of_rule) dates the report, it does not move the data back (tiles.HISTORY);
- question tiles are listed, not answered; text tiles carry their text;
- a pdf rendering is not made (the output says `not_supported`); the email and Teams copies are
  a card with the report's title, date and link, never a figure or a file, sent on the channels
  the recipient has bound and verified (channels/gateway.py); Zalo is not wired. A recipient not
  reached on what they asked for makes the run `partially_sent`.
"""

from __future__ import annotations

import datetime
import html
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, monitor, notify
from libs.dataagent_db.models import org as org_models
from libs.dataagent_db.models import workspace as w
from services.worker_agent import storage
from services.worker_agent.channels import gateway
from services.worker_agent.channels.cards import report_card
from services.worker_agent.events import Event, Handler
from services.worker_agent.exports import Sheet, write_xlsx
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.query.executor import QueryOutcome, bound_sql, run_query
from services.worker_agent.query.guard import SqlRejected
from services.worker_agent.query_runs import Unbuildable, dataset_sql, sql_source
from services.worker_agent.questions import secrets
from services.worker_agent.session_reports import personal_columns

logger = logging.getLogger(__name__)

ROLE = "svc_monitor"
NOTIFY_ROLE = "svc_notify"
EVENT_TYPE = "report_run.requested"
DELIVERED = "report.delivered"
BUCKET = "da-results"
ROWS = 1_000
QUERIED = frozenset({"sql", "table", "chart"})
# The schedules' clock: a cron and an as_of rule are read in the bank's time zone.
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def as_of_for(rule: str | None, today: datetime.date) -> datetime.date | None:
    """The report's date from the schedule's as_of_rule; None for a rule this worker does not
    know (the run then fails saying so, rather than dating the report wrongly)."""
    if rule in (None, "", "today"):
        return today
    if rule == "end_of_previous_day":
        return today - datetime.timedelta(days=1)
    if rule == "end_of_previous_month":
        return today.replace(day=1) - datetime.timedelta(days=1)
    if rule == "end_of_previous_quarter":
        first = today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1)
        return first - datetime.timedelta(days=1)
    return None


@dataclass
class Rendered:
    """One tile as one permission group reads it."""

    title: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    note: str | None = None
    sql: str | None = None
    source: catalog.Source | None = None
    ok: bool = True


async def _tile_sql(
    org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, tile: w.Tile
) -> tuple[catalog.Source, str]:
    spec = tile.spec if isinstance(tile.spec, dict) else {}
    async with scoped_session(QUERY_ROLE, org, user) as session:
        if tile.kind == "table":
            return await dataset_sql(session, space, spec)
        sql = str(spec.get("sql") or "")
        if not sql:
            raise Unbuildable("Ô chưa có câu SQL.")
        return await sql_source(session, space, sql), sql


async def _render(
    org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, tile: w.Tile, run_id: uuid.UUID
) -> Rendered:
    title = tile.title or f"Ô {tile.position}"
    spec = tile.spec if isinstance(tile.spec, dict) else {}
    if tile.kind == "text":
        return Rendered(title, note=str(spec.get("md") or spec.get("text") or ""))
    if tile.kind not in QUERIED or (tile.kind == "chart" and not spec.get("sql")):
        return Rendered(title, note="Ô này chưa được đưa vào báo cáo định kỳ.")
    try:
        source, sql = await _tile_sql(org, space, user, tile)
    except (Unbuildable, SqlRejected, KeyError, ValueError) as exc:
        return Rendered(title, note=str(exc), ok=False)
    outcome: QueryOutcome = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="tile",
        caller_id=tile.id,
        source=source,
        sql=sql,
        secrets=secrets(),
        limit=int(spec.get("limit") or ROWS),
    )
    if not outcome.ok:
        return Rendered(title, note=outcome.error, sql=sql, source=source, ok=False)
    return Rendered(
        title,
        [str(c["name"]) for c in outcome.columns],
        [list(r) for r in outcome.rows],
        sql=sql,
        source=source,
    )


async def _same_rights(
    org: uuid.UUID,
    space: uuid.UUID,
    member: uuid.UUID,
    representative: uuid.UUID,
    tiles: list[Rendered],
) -> bool:
    """Whether `member` would run every query of the rendering as the representative does."""
    for tile in tiles:
        if tile.sql is None or tile.source is None:
            continue
        try:
            async with scoped_session(QUERY_ROLE, org, member) as session:
                theirs = await bound_sql(
                    session,
                    sql=tile.sql,
                    source=tile.source,
                    data_space_id=space,
                    principal_id=member,
                    caller_kind="tile",
                )
            async with scoped_session(QUERY_ROLE, org, representative) as session:
                ours = await bound_sql(
                    session,
                    sql=tile.sql,
                    source=tile.source,
                    data_space_id=space,
                    principal_id=representative,
                    caller_kind="tile",
                )
        except SqlRejected:
            return False
        if theirs.executed != ours.executed:
            return False
    return True


def render_html(title: str, as_of: datetime.date, tiles: list[Rendered]) -> bytes:
    parts = [
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title></head><body>",
        f"<h1>{html.escape(title)}</h1><p>Số liệu tính đến ngày {as_of:%d/%m/%Y}.</p>",
    ]
    for tile in tiles:
        parts.append(f"<h2>{html.escape(tile.title)}</h2>")
        if tile.note:
            parts.append(f"<p>{html.escape(tile.note)}</p>")
        if tile.columns:
            head = "".join(f"<th>{html.escape(c)}</th>" for c in tile.columns)
            body = "".join(
                "<tr>"
                + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in row)
                + "</tr>"
                for row in tile.rows
            )
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts).encode()


def render_xlsx(as_of: datetime.date, tiles: list[Rendered]) -> bytes:
    sheets = [
        Sheet(t.title or f"Ô {i}", t.columns, t.rows) for i, t in enumerate(tiles, 1) if t.columns
    ] or [Sheet("Báo cáo", ["Ghi chú"], [[t.note or ""] for t in tiles])]
    return write_xlsx(sheets, [["Số liệu tính đến ngày", as_of.strftime("%d/%m/%Y")]])


async def _recipients(
    org: uuid.UUID, schedule_id: uuid.UUID
) -> dict[str, list[tuple[uuid.UUID, list[str]]]]:
    """Permission group → its active users and the channels each is reached on; a group
    recipient stands for its members."""
    async with scoped_session(ROLE, org) as session:
        rows = (
            await session.scalars(
                select(monitor.ReportRecipient).where(
                    monitor.ReportRecipient.report_schedule_id == schedule_id
                )
            )
        ).all()
        groups: dict[str, dict[uuid.UUID, list[str]]] = {}
        for row in rows:
            if row.principal_type == "user":
                users = [row.principal_id]
            else:
                users = list(
                    (
                        await session.scalars(
                            select(org_models.UserGroupMember.user_id).where(
                                org_models.UserGroupMember.group_id == row.principal_id
                            )
                        )
                    ).all()
                )
            active = (
                set(
                    (
                        await session.scalars(
                            select(org_models.AppUser.id).where(
                                org_models.AppUser.id.in_(users),
                                org_models.AppUser.status == "active",
                            )
                        )
                    ).all()
                )
                if users
                else set()
            )
            members = groups.setdefault(row.permission_group, {})
            for user in sorted(active, key=str):
                members.setdefault(user, [])
                members[user] = sorted(set(members[user]) | set(row.channels or []))
    return {g: sorted(m.items(), key=lambda kv: str(kv[0])) for g, m in groups.items()}


async def _checks(
    org: uuid.UUID, owner: uuid.UUID, space: uuid.UUID, renderings: dict[str, list[Rendered]]
) -> dict[str, dict[str, str]]:
    """The pre-send checks of a report (require_publish_check): every tile of every rendering
    ran, and none reads a column the catalogue labels personal data (BR-35)."""
    failed_tiles = sorted({t.title for tiles in renderings.values() for t in tiles if not t.ok})
    pii: set[str] = set()
    async with scoped_session(QUERY_ROLE, org, owner) as session:
        for tiles in renderings.values():
            for t in tiles:
                if t.sql and t.source:
                    try:
                        found = await personal_columns(session, owner, t.sql, t.source, space)
                    except SqlRejected:
                        found = {"không xác định được cột"}
                    pii |= {f"{t.title}: {c}" for c in found}
    return {
        "grounded": {
            "status": "fail",
            "title": "Số có nguồn",
            "detail": "Ô chạy lỗi: " + ", ".join(failed_tiles),
        }
        if failed_tiles
        else {"status": "pass", "title": "Số có nguồn", "detail": "Mọi ô đều chạy được"},
        "personal_data": {
            "status": "fail",
            "title": "Dữ liệu cá nhân",
            "detail": "; ".join(sorted(pii)),
        }
        if pii
        else {
            "status": "pass",
            "title": "Dữ liệu cá nhân",
            "detail": "Không ô nào đọc cột dữ liệu cá nhân",
        },
    }


async def _tell(
    org: uuid.UUID,
    users: list[uuid.UUID],
    space: uuid.UUID,
    run_id: uuid.UUID,
    title: str,
    body: str | None,
    kind: str = "report",
) -> None:
    async with scoped_session(NOTIFY_ROLE, org) as session:
        for user in users:
            session.add(
                notify.Notification(
                    id=uuid7(),
                    org_id=org,
                    user_id=user,
                    data_space_id=space,
                    kind=kind,
                    title=title,
                    # The body holds no figures (notify.notification.body): the report is read
                    # through GET /v1/report-runs/{id}, which shows each reader their rendering.
                    body=body,
                    link=f"/reports/runs/{run_id}",
                    source_ref=run_id,
                )
            )


async def _deliver(
    org: uuid.UUID,
    user: uuid.UUID,
    channels: list[str],
    space: uuid.UUID,
    run_id: uuid.UUID,
    title: str,
    as_of: datetime.date,
) -> bool:
    """Tell one recipient on the channels their recipient row names: in the app, and on email or
    Teams when they have bound and verified one (the channel gateway's delivery, with its
    hand-over to another bound channel and a channel_delivery row per attempt). False when an
    outside channel was asked for and none took the card: the run is then partially_sent."""
    notification_id = uuid7()
    async with scoped_session(NOTIFY_ROLE, org) as session:
        session.add(
            notify.Notification(
                id=notification_id,
                org_id=org,
                user_id=user,
                data_space_id=space,
                kind="report",
                title=f"{title}: báo cáo ngày {as_of:%d/%m/%Y}",
                # The body holds no figures (notify.notification.body): the report is read
                # through GET /v1/report-runs/{id}, which shows each reader their rendering.
                body=None,
                link=f"/reports/runs/{run_id}",
                source_ref=run_id,
            )
        )
    external = [c for c in channels if c != "in_app"]
    if not external:
        return True
    card = report_card(
        title=title,
        as_of=f"{as_of:%d/%m/%Y}",
        link=f"{gateway.app_url().rstrip('/')}/reports/runs/{run_id}",
    )
    sent = await gateway.deliver(
        org,
        user,
        external,
        await gateway.bindings(org, user),
        card,
        notification_id=notification_id,
        hitl_request_id=None,
    )
    # The gateway sends the card once, handing over to another bound channel when one fails:
    # reached when one of them took it. None bound, or Zalo only, sends nothing.
    return any(status == "sent" for _, status in sent)


async def _finish(
    org: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    checks: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> None:
    async with scoped_session(ROLE, org) as session:
        run = await session.get(monitor.ReportRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return
        run.status, run.checks, run.outputs = status, checks, outputs
        run.finished_at = datetime.datetime.now(datetime.UTC)
        session.add(
            monitor.Outbox(
                id=uuid7(),
                org_id=org,
                aggregate_type="report_run",
                aggregate_id=run_id,
                event_type=DELIVERED,
                payload={"report_run_id": str(run_id), "status": status},
            )
        )


async def run_requested(event: Event) -> None:
    org = event.org_id
    run_id = uuid.UUID(str(event.payload["report_run_id"]))
    async with scoped_session(ROLE, org) as session:
        run = await session.get(monitor.ReportRun, run_id)
        if run is None or run.status != "running":
            return  # redelivered, or already ended
        schedule = await session.get(monitor.ReportSchedule, run.report_schedule_id)
    if schedule is None:
        await _finish(org, run_id, "failed", {"error": "Không còn lịch báo cáo."}, [])
        return
    if run.kind != "report" or schedule.workspace_id is None:
        await _finish(
            org,
            run_id,
            "failed",
            {"error": "Bản tin lãnh đạo (digest) chưa được dựng; báo cáo này chưa gửi."},
            [],
        )
        return
    owner, space, as_of = schedule.owner_id, schedule.data_space_id, run.as_of
    async with scoped_session("svc_workspace", org, owner) as session:
        tiles = list(
            (
                await session.scalars(
                    select(w.Tile)
                    .where(
                        w.Tile.workspace_id == schedule.workspace_id, w.Tile.deleted_at.is_(None)
                    )
                    .order_by(w.Tile.position)
                )
            ).all()
        )
    groups = await _recipients(org, schedule.id)
    renderings: dict[str, list[Rendered]] = {}
    left_out: list[str] = []
    readers: dict[str, list[tuple[uuid.UUID, list[str]]]] = {}
    for group, members in groups.items():
        if not members:
            continue
        representative = members[0][0]
        rendered = [await _render(org, space, representative, t, run_id) for t in tiles]
        renderings[group] = rendered
        kept = [members[0]]
        for user, channels in members[1:]:
            if await _same_rights(org, space, user, representative, rendered):
                kept.append((user, channels))
            else:
                left_out.append(str(user))
        readers[group] = kept
    checks: dict[str, Any] = {}
    if schedule.require_publish_check:
        checks = await _checks(org, owner, space, renderings)
    if left_out:
        checks["rights"] = {
            "status": "fail",
            "title": "Quyền người nhận",
            "detail": f"{len(left_out)} người nhận không cùng quyền với nhóm của mình nên không nhận bản này",
        }
    if any(
        c.get("status") == "fail" for k, c in checks.items() if k in ("grounded", "personal_data")
    ):
        # Do not send (UC-08-03): the owner reviews what failed.
        await _tell(
            org,
            [owner],
            space,
            run_id,
            f"{schedule.title}: chưa gửi vì không qua bước kiểm",
            None,
            "alert",
        )
        await _finish(org, run_id, "blocked_by_checks", checks, [])
        return
    outputs: list[dict[str, Any]] = []
    sent_to: list[uuid.UUID] = []
    missed = bool(left_out)
    for group, rendered in renderings.items():
        for fmt in schedule.formats or ["html"]:
            item: dict[str, Any] = {"permission_group": group, "format": fmt}
            if fmt not in CONTENT_TYPES:
                outputs.append({**item, "delivery": "not_supported"})
                missed = True
                continue
            body = (
                render_html(schedule.title, as_of, rendered)
                if fmt == "html"
                else render_xlsx(as_of, rendered)
            )
            key = f"report-runs/{run_id}/{group}.{fmt}"
            try:
                await storage.store().put(BUCKET, key, body, CONTENT_TYPES[fmt])
            except storage.StoreUnavailable as exc:
                logger.warning("worker-agent | report run %s: %s", run_id, exc)
                outputs.append({**item, "delivery": "store_unavailable"})
                missed = True
                continue
            outputs.append({**item, "object_key": key, "delivery": "stored"})
        if any(o.get("delivery") == "stored" for o in outputs if o["permission_group"] == group):
            for user, channels in readers.get(group, []):
                if await _deliver(org, user, channels, space, run_id, schedule.title, as_of):
                    sent_to.append(user)
                else:
                    missed = True
    if left_out:
        await _tell(
            org,
            [owner],
            space,
            run_id,
            f"{schedule.title}: có người nhận bị loại",
            checks["rights"]["detail"],
            "alert",
        )
    stored = any(o.get("delivery") == "stored" for o in outputs)
    status = "failed" if not stored or not sent_to else "partially_sent" if missed else "sent"
    await _finish(org, run_id, status, checks, outputs)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: run_requested}
