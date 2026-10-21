"""Session reports (UC-02-07): the draft drawn from a workspace's tiles, and its four checks.

`session_report.created` (POST /v1/workspaces/{id}/reports) writes the draft from the latest
snapshot of each tile named, as the markdown the review screen reads (frontend
src/lib/v1/sessions.ts, readReportDocument), to MinIO da-results at the report's body_ref. Its
figures are the rows of the snapshots' query runs, and only of runs the author ran: a snapshot is
every member's while its queries ran with one person's rights (tiles.py), so someone else's run is
not written into the author's report; the tile is listed as ungrounded instead. Each tile drawn
becomes a lineage edge tile -> report whose via_ref is the snapshot used (none when it had no
usable one), so the checks read the very runs the draft was written from.

`session_report.status_changed` with status `checking` (POST /v1/session-reports/{id}:check) runs
the four checks and sets the report `ready` or `failed_checks`. A check that cannot be judged
fails; it never passes by default:
- grounded: every tile drawn had an ok snapshot whose runs the author ran, succeeded, and whose
  rows are still kept;
- filed: a figure of a filed period (a filed period close of this Data Space on the run's as_of,
  with a published result of one of the run's metric versions) must have been read from the filed
  snapshot (the run's period_close_id). The design sets no tolerance for a live figure against the
  filed one, so a live figure there fails rather than being compared;
- rights: everyone who may read the report (the workspace's owner, its shares and participants,
  and the organisation's approvers) would run each of its queries as the same SQL as the author
  (executor.bound_sql), so reads the same rows and nothing more;
- personal_data: no figure's query reads a column the catalogue labels personal data (pii_label),
  anywhere in it (projection, filter, join, inside a function) or through a whole-row read.

The draft is written before the checks, so the api serves its document to the author alone until
the report is ready (workspace/routes.py, get_session_report_document).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import any_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlglot import exp

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, period, pipeline
from libs.dataagent_db.models import org as org_models
from libs.dataagent_db.models import query as q
from libs.dataagent_db.models import workspace as w
from services.worker_agent import storage
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.query.executor import bound_sql
from services.worker_agent.query.guard import SqlRejected, check_read_only, dialect_of, tables_of
from services.worker_agent.query.scope import datasets_read

logger = logging.getLogger(__name__)

ROLE = "svc_workspace"
LINEAGE_ROLE = "svc_pipeline"
CREATED = "session_report.created"
STATUS_CHANGED = "session_report.status_changed"
BUCKET = "da-results"  # services/api/modules/workspace/report_storage.py reads it from here
ROWS_PER_RUN = 20  # figure lines written per query run; the rest stay in the tile
CHECK_TITLES = {
    "grounded": "Số có nguồn",
    "filed": "Khớp số đã nộp",
    "rights": "Quyền người nhận",
    "personal_data": "Dữ liệu cá nhân",
}


@dataclass
class Drawn:
    """One tile as the report holds it: the snapshot drawn from and its runs, with their rows."""

    tile: w.Tile
    snapshot: w.TileSnapshot | None
    runs: list[q.QueryRun] = field(default_factory=list)
    results: dict[uuid.UUID, q.QueryResult] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.tile.title or f"Ô {self.tile.position}"


def _uuid(value: Any) -> uuid.UUID:
    return uuid.UUID(str(value))


async def _runs(
    org: uuid.UUID, author: uuid.UUID, run_ids: Iterable[uuid.UUID]
) -> tuple[dict[uuid.UUID, q.QueryRun], dict[uuid.UUID, q.QueryResult]]:
    wanted = list(set(run_ids))
    if not wanted:
        return {}, {}
    async with scoped_session(QUERY_ROLE, org, author) as session:
        runs = (await session.scalars(select(q.QueryRun).where(q.QueryRun.id.in_(wanted)))).all()
        results = (
            await session.scalars(
                select(q.QueryResult).where(
                    q.QueryResult.query_run_id.in_(wanted),
                    q.QueryResult.expires_at > func.now(),
                )
            )
        ).all()
    return {r.id: r for r in runs}, {r.query_run_id: r for r in results}


def _usable(run: q.QueryRun | None, author: uuid.UUID, results: dict[uuid.UUID, Any]) -> bool:
    return (
        run is not None
        and run.principal_id == author
        and run.status == "succeeded"
        and run.id in results
    )


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def _figure_lines(drawn: Drawn, author: uuid.UUID) -> list[str]:
    snapshot = drawn.snapshot
    if snapshot is None or snapshot.status != "ok":
        reason = snapshot.error if snapshot is not None and snapshot.error else "chưa có số"
        return [f"- {drawn.title}: {reason} | ungrounded"]
    if not snapshot.query_run_ids:
        return [f"- {drawn.title}: ô không có số truy vấn | ungrounded"]
    lines: list[str] = []
    by_id = {r.id: r for r in drawn.runs}
    for run_id in snapshot.query_run_ids:
        run = by_id.get(run_id)
        if not _usable(run, author, drawn.results):
            lines.append(f"- {drawn.title}: số do người khác chạy hoặc đã hết hạn | ungrounded")
            continue
        result = drawn.results[run_id]
        names = [str(c.get("name")) for c in result.columns or [] if isinstance(c, dict)]
        rows = result.rows or []
        for row in rows[:ROWS_PER_RUN]:
            cells = "; ".join(f"{n} = {_cell(v)}" for n, v in zip(names, row, strict=False))
            lines.append(f"- {drawn.title}: {cells} | grounded")
        if len(rows) > ROWS_PER_RUN or result.truncated:
            # Not a figure line (no trailing state): the review screen reads it as nothing.
            lines.append(f"- {drawn.title}: còn dòng khác, xem trong ô")
    return lines


def render(title: str, drawn: list[Drawn], author: uuid.UUID) -> str:
    grounded = [line for d in drawn for line in _figure_lines(d, author)]
    as_of = sorted({str(d.snapshot.as_of) for d in drawn if d.snapshot and d.snapshot.as_of})
    when = f", số liệu tại ngày {', '.join(as_of)}" if as_of else ""
    return "\n".join(
        [
            f"# {title}",
            "",
            f"Báo cáo soạn từ {len(drawn)} ô của phiên, theo lần làm mới gần nhất của từng ô{when}.",
            "",
            "## Số liệu",
            *grounded,
            "",
        ]
    )


async def _draw(
    session: Any,
    org: uuid.UUID,
    author: uuid.UUID,
    tiles: list[w.Tile],
    snapshot_ids: dict[uuid.UUID, uuid.UUID | None],
) -> list[Drawn]:
    wanted = [s for s in snapshot_ids.values() if s is not None]
    snapshots = (
        {
            s.id: s
            for s in (
                await session.scalars(select(w.TileSnapshot).where(w.TileSnapshot.id.in_(wanted)))
            ).all()
        }
        if wanted
        else {}
    )
    drawn = [Drawn(t, snapshots.get(snapshot_ids.get(t.id))) for t in tiles]
    runs, results = await _runs(
        org, author, (r for d in drawn if d.snapshot for r in d.snapshot.query_run_ids)
    )
    for d in drawn:
        if d.snapshot is not None:
            d.runs = [runs[r] for r in d.snapshot.query_run_ids if r in runs]
            d.results = {r.id: results[r.id] for r in d.runs if r.id in results}
    return drawn


async def report_created(event: Event) -> None:
    p = event.payload
    org, report_id = event.org_id, _uuid(p["session_report_id"])
    author, space = _uuid(p["requested_by"]), _uuid(p["data_space_id"])
    tile_ids = [_uuid(t) for t in p.get("tile_ids") or []]
    async with scoped_session(ROLE, org, author) as session:
        report = await session.get(w.SessionReport, report_id)
        # A redelivered event after the checks began leaves the report as it is.
        if report is None or report.status != "draft":
            return
        found = {
            t.id: t
            for t in (
                await session.scalars(
                    select(w.Tile).where(
                        w.Tile.id.in_(tile_ids),
                        w.Tile.workspace_id == report.workspace_id,
                        w.Tile.deleted_at.is_(None),
                    )
                )
            ).all()
        }
        tiles = [found[t] for t in tile_ids if t in found]
        drawn = await _draw(
            session, org, author, tiles, {t.id: t.latest_snapshot_id for t in tiles}
        )
        title, body_ref = report.title, report.body_ref
    try:
        await storage.store().put(
            BUCKET, body_ref, render(title, drawn, author).encode(), "text/markdown; charset=utf-8"
        )
    except storage.StoreUnavailable as exc:
        # The report stays a draft without a body; its check fails grounding, saying so.
        logger.warning("worker-agent | session report %s not drafted: %s", report_id, exc)
        return
    if not drawn:
        return
    async with scoped_session(LINEAGE_ROLE, org, author) as session:
        await session.execute(
            insert(pipeline.LineageEdge)
            .values(
                [
                    {
                        "id": uuid7(),
                        "org_id": org,
                        "data_space_id": space,
                        "from_kind": "tile",
                        "from_id": d.tile.id,
                        "to_kind": "report",
                        "to_id": report_id,
                        "via": "snapshot",
                        "via_ref": d.snapshot.id if d.snapshot else None,
                    }
                    for d in drawn
                ]
            )
            .on_conflict_do_nothing()
        )


def _verdict(failures: list[str], passed: str) -> dict[str, str]:
    if failures:
        return {"status": "fail", "detail": "; ".join(dict.fromkeys(failures))}
    return {"status": "pass", "detail": passed}


def _grounded(drawn: list[Drawn], author: uuid.UUID) -> dict[str, str]:
    if not drawn:
        return _verdict(["Bản nháp chưa được soạn hoặc không có ô nào"], "")
    failures = []
    for d in drawn:
        if d.snapshot is None or d.snapshot.status != "ok" or not d.snapshot.query_run_ids:
            failures.append(f"{d.title}: không có số đã chạy")
            continue
        by_id = {r.id: r for r in d.runs}
        if not all(_usable(by_id.get(r), author, d.results) for r in d.snapshot.query_run_ids):
            failures.append(f"{d.title}: có số không do người soạn chạy, lỗi hoặc đã hết hạn")
    return _verdict(failures, "Mọi số đều lấy từ một lần chạy truy vấn còn lưu")


async def _filed(session: Any, space: uuid.UUID, drawn: list[Drawn]) -> dict[str, str]:
    failures = []
    for d in drawn:
        for run in d.runs:
            if run.period_close_id is not None or not run.metric_version_ids or run.as_of is None:
                continue
            filed = await session.scalar(
                select(period.PublishedResult.id)
                .join(
                    period.PeriodClose,
                    period.PeriodClose.id == period.PublishedResult.period_close_id,
                )
                .where(
                    period.PeriodClose.data_space_id == space,
                    period.PeriodClose.status == "filed",
                    period.PeriodClose.business_date == run.as_of,
                    period.PublishedResult.metric_version_id.in_(run.metric_version_ids),
                )
                .limit(1)
            )
            if filed is not None:
                failures.append(
                    f"{d.title}: kỳ {run.as_of} đã nộp nhưng số được tính trực tiếp,"
                    " không lấy từ bản đã nộp"
                )
    return _verdict(failures, "Không số nào lệch nguồn với số đã nộp")


async def _readers(session: Any, item: w.Workspace, author: uuid.UUID) -> set[uuid.UUID]:
    """Who may read the report besides its author (services/api/modules/workspace/routes.py,
    _visible and _readable_reports): owner, live shares (a group's members), participants, and
    the organisation's approvers, who read it once it waits for them."""
    share = w.WorkspaceShare
    shares = (
        await session.execute(
            select(share.principal_type, share.principal_id).where(
                share.workspace_id == item.id,
                or_(share.expires_at.is_(None), share.expires_at > func.now()),
            )
        )
    ).all()
    users = {item.owner_id} | {pid for kind, pid in shares if kind == "user"}
    groups = [pid for kind, pid in shares if kind == "group"]
    if groups:
        users |= set(
            (
                await session.scalars(
                    select(org_models.UserGroupMember.user_id).where(
                        org_models.UserGroupMember.group_id.in_(groups)
                    )
                )
            ).all()
        )
    users |= set(
        (
            await session.scalars(
                select(w.WsParticipant.principal_id).where(
                    w.WsParticipant.workspace_id == item.id,
                    w.WsParticipant.principal_type == "user",
                )
            )
        ).all()
    )
    users |= set(
        (
            await session.scalars(
                select(org_models.UserRole.user_id)
                .join(org_models.Role, org_models.Role.id == org_models.UserRole.role_id)
                .where(literal("approve") == any_(org_models.Role.capabilities))
            )
        ).all()
    )
    return users - {author}


async def _bound(
    org: uuid.UUID, user: uuid.UUID, run: q.QueryRun, source: catalog.Source
) -> str | None:
    """The SQL `user` would run for this run's query, or None when they may not run it."""
    try:
        async with scoped_session(QUERY_ROLE, org, user) as session:
            bound = await bound_sql(
                session,
                sql=run.sql_generated,
                source=source,
                data_space_id=run.data_space_id,
                principal_id=user,
                caller_kind=run.caller_kind,
            )
    except SqlRejected:
        return None
    return bound.executed


async def _rights(
    org: uuid.UUID,
    author: uuid.UUID,
    readers: set[uuid.UUID],
    names: dict[uuid.UUID, str],
    drawn: list[Drawn],
    sources: dict[uuid.UUID, catalog.Source],
) -> dict[str, str]:
    failures = []
    for d in drawn:
        for run in d.runs:
            source = sources.get(run.source_id) if run.source_id else None
            if source is None or not run.sql_generated:
                failures.append(f"{d.title}: không dựng lại được truy vấn để so quyền")
                continue
            own = await _bound(org, author, run, source)
            if own is None:
                failures.append(f"{d.title}: người soạn không còn quyền đọc số này")
                continue
            for reader in sorted(readers, key=str):
                theirs = await _bound(org, reader, run, source)
                who = names.get(reader, str(reader))
                if theirs is None:
                    failures.append(f"{who} không được đọc số của {d.title}")
                elif theirs != own:
                    failures.append(f"{who} có phạm vi hoặc chính sách khác ở {d.title}")
    return _verdict(failures, "Mọi người đọc báo cáo đều được xem đúng các số này")


def _referenced(sql: str, source: catalog.Source) -> tuple[set[str], bool]:
    """The column names `sql` reads anywhere (projection, filter, join, inside functions), and
    whether it reads whole rows: `*`, `t.*`, or a table or alias used as a value
    (row_to_json(t)). Output names prove nothing, since an alias renames a column; count(*)
    reads no column."""
    dialect = dialect_of(source.kind)
    tree = check_read_only(sql, dialect)
    tables = {t.name.lower() for t in tree.find_all(exp.Table)} | {
        t.alias_or_name.lower() for t in tree.find_all(exp.Table)
    }
    names: set[str] = set()
    whole_row = False
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star) or (
            column.name.lower() in tables and not column.table
        ):
            whole_row = True
        names.add(column.name.lower())
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count | exp.Column):
            whole_row = True
    return names, whole_row


async def _personal_data(
    session: Any,
    author: uuid.UUID,
    drawn: list[Drawn],
    sources: dict[uuid.UUID, catalog.Source],
) -> dict[str, str]:
    failures = []
    for d in drawn:
        for run in d.runs:
            source = sources.get(run.source_id) if run.source_id else None
            if source is None or not run.sql_generated:
                failures.append(f"{d.title}: không xác định được cột của số")
                continue
            try:
                names, whole_row = _referenced(run.sql_generated, source)
                datasets = await datasets_read(
                    session,
                    data_space_id=run.data_space_id,
                    principal_id=author,
                    source=source,
                    tables=tables_of_sql(run.sql_generated, source),
                )
            except SqlRejected:
                failures.append(f"{d.title}: không xác định được bảng của số")
                continue
            if not datasets:
                continue
            labelled = (
                await session.execute(
                    select(catalog.ColumnMeta.name, catalog.ColumnMeta.pii_label).where(
                        catalog.ColumnMeta.dataset_id.in_([ds.id for ds in datasets]),
                        catalog.ColumnMeta.pii_label.is_not(None),
                    )
                )
            ).all()
            # Matched by name across every table the query reads, not resolved to one: a name
            # two tables share fails for both, which errs on the side of the check.
            hits = sorted(
                {f"{n} ({label})" for n, label in labelled if whole_row or n.lower() in names}
            )
            if hits:
                failures.append(f"{d.title}: có cột dữ liệu cá nhân {', '.join(hits)}")
    return _verdict(failures, "Không số nào lấy từ cột dữ liệu cá nhân")


def tables_of_sql(sql: str, source: catalog.Source) -> set[tuple[str | None, str]]:
    dialect = dialect_of(source.kind)
    return tables_of(check_read_only(sql, dialect), dialect)


async def check_report(org: uuid.UUID, report_id: uuid.UUID) -> dict[str, dict[str, str]] | None:
    """The four checks of a report in `checking`, or None when it is not (anymore)."""
    async with scoped_session(ROLE, org) as session:
        report = await session.get(w.SessionReport, report_id)
        if report is None or report.status != "checking" or report.created_by is None:
            return None
        author = report.created_by
        item = await session.get(w.Workspace, report.workspace_id)
        if item is None:
            return None
    async with scoped_session(LINEAGE_ROLE, org, author) as session:
        edges = (
            (
                await session.execute(
                    select(pipeline.LineageEdge.from_id, pipeline.LineageEdge.via_ref).where(
                        pipeline.LineageEdge.to_kind == "report",
                        pipeline.LineageEdge.to_id == report_id,
                        pipeline.LineageEdge.from_kind == "tile",
                        pipeline.LineageEdge.valid_to.is_(None),
                    )
                )
            )
            .tuples()
            .all()
        )
    async with scoped_session(ROLE, org, author) as session:
        tiles = (
            (
                await session.scalars(
                    select(w.Tile)
                    .where(w.Tile.id.in_([tile for tile, _ in edges]))
                    .order_by(w.Tile.position)
                )
            ).all()
            if edges
            else []
        )
        drawn = await _draw(session, org, author, list(tiles), dict(edges))
        readers = await _readers(session, item, author)
        names: dict[uuid.UUID, str] = {}
        if readers:
            rows = await session.execute(
                select(org_models.AppUser.id, org_models.AppUser.username).where(
                    org_models.AppUser.id.in_(readers)
                )
            )
            names = dict(rows.tuples().all())
    source_ids = {r.source_id for d in drawn for r in d.runs if r.source_id}
    async with scoped_session(QUERY_ROLE, org, author) as session:
        sources = (
            {
                s.id: s
                for s in (
                    await session.scalars(
                        select(catalog.Source).where(catalog.Source.id.in_(source_ids))
                    )
                ).all()
            }
            if source_ids
            else {}
        )
        filed = await _filed(session, item.data_space_id, drawn)
        personal = await _personal_data(session, author, drawn, sources)
    checks = {
        "grounded": _grounded(drawn, author),
        "filed": filed,
        "rights": await _rights(org, author, readers, names, drawn, sources),
        "personal_data": personal,
    }
    if not drawn:
        # Nothing was drafted, so nothing could be judged: every check says so, none passes.
        for name in ("filed", "rights", "personal_data"):
            checks[name] = _verdict(["Chưa có số để kiểm"], "")
    return {name: {**value, "title": CHECK_TITLES[name]} for name, value in checks.items()}


async def report_status_changed(event: Event) -> None:
    p = event.payload
    # The api emits this for every move (publish, approval); only a check request is ours.
    if p.get("status") != "checking":
        return
    org, report_id = event.org_id, _uuid(p["session_report_id"])
    checks = await check_report(org, report_id)
    if checks is None:
        return
    failed = any(value["status"] != "pass" for value in checks.values())
    async with scoped_session(ROLE, org) as session:
        report = await session.get(w.SessionReport, report_id, with_for_update=True)
        if report is None or report.status != "checking":
            return
        report.checks = checks
        report.status = "failed_checks" if failed else "ready"
        report.updated_at = datetime.datetime.now(datetime.UTC)
        session.add(
            w.Outbox(
                id=uuid7(),
                org_id=org,
                aggregate_type="session_report",
                aggregate_id=report.id,
                event_type=STATUS_CHANGED,
                payload={
                    "session_report_id": str(report.id),
                    "workspace_id": str(report.workspace_id),
                    "version": report.version,
                    "status": report.status,
                    "failed_checks": sorted(n for n, v in checks.items() if v["status"] != "pass"),
                },
            )
        )


HANDLERS: dict[str, Handler] = {CREATED: report_created, STATUS_CHANGED: report_status_changed}
