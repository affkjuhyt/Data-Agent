"""Running a workspace's tiles (UC-02-02): the `tile.run_requested` and
`workspace.refresh_requested` handlers.

The api sends the tiles in dependency order, roots first (services/api/modules/workspace/
routes.py). Each runnable tile gets a new workspace.tile_snapshot and becomes its
latest_snapshot_id, no longer stale. Queries run with the rights of whoever asked (BR-07),
through the executor's checks (Data Space tables only, no unapplied access policy).

What a tile's spec holds decides how it runs:
- `sql` {sql}: the caller's read-only SQL, on the source whose datasets hold its tables;
- `table` {dataset_id, filters}: a dataset's rows under column filters;
- `question` {question}: the single-metric answer path, recorded;
- `chart` {sql} runs like `sql`; a chart without SQL draws its first dependency's data, so its
  snapshot reuses that tile's query runs;
- `text`, `skill_run`, `agent_result` have nothing to query here (a skill run is the skill
  worker's) and are left as they are.

A snapshot is the tile's latest for every member of the workspace, while its queries ran with
one person's rights, so it holds no data: the runs, and a summary of shape only (columns, row
count, figure count). The values are read through GET /v1/query-runs/{id}/rows, which serves them
to whoever ran the query and no one else.

A tile whose dependency failed is not run: its snapshot is `failed` and says why (UC-02-02 step
2). A knowledge time before today needs the history route, so such a refresh fails each tile
saying so instead of running live. A redelivered event writes one more snapshot, which becomes
the latest; nothing else changes.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import workspace as w
from libs.sql_guard import SqlRejected
from services.worker_agent import private
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query_runs import HISTORY, Unbuildable, dataset_sql, sql_source
from services.worker_agent.questions import secrets

logger = logging.getLogger(__name__)

ROLE = "svc_workspace"
TILE_RUN = "tile.run_requested"
REFRESH = "workspace.refresh_requested"
NOT_QUERIED = frozenset({"text", "skill_run", "agent_result"})
DEPENDENCY_FAILED = "Ô phụ thuộc chạy lỗi nên ô này không chạy."
TABLE_ROWS = 1_000


@dataclass
class Snapshot:
    status: str  # ok, failed
    query_run_ids: list[uuid.UUID] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    error: str | None = None


def _of_query(outcome: QueryOutcome) -> Snapshot:
    if not outcome.ok:
        return Snapshot("failed", [outcome.query_run_id], error=outcome.error)
    return Snapshot(
        "ok",
        [outcome.query_run_id],
        summary={
            "columns": [c["name"] for c in outcome.columns],
            "row_count": len(outcome.rows),
            "truncated": outcome.truncated,
        },
    )


async def _query(
    tile: w.Tile, org: uuid.UUID, space: uuid.UUID, asker: uuid.UUID, as_of: datetime.date | None
) -> Snapshot:
    spec = tile.spec if isinstance(tile.spec, dict) else {}
    try:
        async with scoped_session(QUERY_ROLE, org, asker) as session:
            if tile.kind == "table":
                source, sql = await dataset_sql(session, space, spec)
            else:
                sql = str(spec.get("sql") or "")
                if not sql:
                    raise Unbuildable("Ô chưa có câu SQL.")
                source = await sql_source(session, space, sql)
    except (Unbuildable, SqlRejected, KeyError, ValueError) as exc:
        return Snapshot("failed", error=str(exc))
    outcome = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=asker,
        caller_kind="tile",
        caller_id=tile.id,
        source=source,
        sql=sql,
        secrets=secrets(),
        as_of=as_of,
        limit=int(spec.get("limit") or TABLE_ROWS),
    )
    return _of_query(outcome)


async def _question(
    tile: w.Tile, org: uuid.UUID, space: uuid.UUID, asker: uuid.UUID, as_of: datetime.date | None
) -> Snapshot:
    spec = tile.spec if isinstance(tile.spec, dict) else {}
    event, data = await private.answer(
        {
            "id": str(tile.id),
            "org_id": str(org),
            "owner_id": str(asker),
            "text": str(spec.get("question") or ""),
            "as_of": as_of.isoformat() if as_of else None,
            "data_space_id": str(space),
        },
        private=False,
        caller_kind="tile",
        caller_id=tile.id,
    )
    if event != "answer":
        return Snapshot("failed", error=str(data.get("message")))
    runs = sorted({uuid.UUID(f["query_run_id"]) for f in data["figures"]}, key=str)
    # No text or value: the snapshot is the tile's for every member, the figures are the asker's.
    return Snapshot("ok", runs, summary={"figure_count": len(data["figures"])})


async def run_tiles(
    org: uuid.UUID,
    workspace_id: uuid.UUID,
    tile_ids: list[uuid.UUID],
    asker: uuid.UUID,
    as_of: datetime.date | None,
) -> dict[uuid.UUID, Snapshot]:
    async with scoped_session(ROLE, org, asker) as session:
        item = await session.get(w.Workspace, workspace_id)
        rows = {
            t.id: t
            for t in (
                await session.scalars(
                    select(w.Tile).where(
                        w.Tile.workspace_id == workspace_id,
                        w.Tile.id.in_(tile_ids),
                        w.Tile.deleted_at.is_(None),
                    )
                )
            ).all()
        }
    if item is None or item.deleted_at is not None:
        return {}
    space = item.data_space_id
    history = as_of is not None and as_of < datetime.date.today()

    done: dict[uuid.UUID, Snapshot] = {}
    for tile_id in tile_ids:
        tile = rows.get(tile_id)
        if tile is None or tile.kind in NOT_QUERIED:
            continue
        upstream = [done[d] for d in tile.depends_on or [] if d in done]
        if any(s.status == "failed" for s in upstream):
            snap = Snapshot("failed", error=DEPENDENCY_FAILED)
        elif history:
            snap = Snapshot("failed", error=HISTORY)
        elif tile.kind == "question":
            snap = await _question(tile, org, space, asker, as_of)
        elif tile.kind == "chart" and not (isinstance(tile.spec, dict) and tile.spec.get("sql")):
            snap = await _drawn_from(tile, done, org, asker)
        else:
            snap = await _query(tile, org, space, asker, as_of)
        done[tile_id] = snap
        await _record(tile, snap, org, asker, as_of)
    return done


async def _drawn_from(
    tile: w.Tile, done: dict[uuid.UUID, Snapshot], org: uuid.UUID, asker: uuid.UUID
) -> Snapshot:
    if not tile.depends_on:
        return Snapshot("failed", error="Biểu đồ chưa có SQL và không phụ thuộc ô nào.")
    source_id = tile.depends_on[0]
    if source_id in done:
        upstream = done[source_id]
        return Snapshot(upstream.status, upstream.query_run_ids, upstream.summary, upstream.error)
    async with scoped_session(ROLE, org, asker) as session:
        latest = await session.scalar(
            select(w.TileSnapshot)
            .join(w.Tile, w.Tile.latest_snapshot_id == w.TileSnapshot.id)
            .where(w.Tile.id == source_id)
        )
    if latest is None or latest.status == "failed":
        return Snapshot("failed", error=DEPENDENCY_FAILED)
    return Snapshot("ok", list(latest.query_run_ids), latest.summary)


async def _record(
    tile: w.Tile, snap: Snapshot, org: uuid.UUID, asker: uuid.UUID, as_of: datetime.date | None
) -> None:
    async with scoped_session(ROLE, org, asker) as session:
        row = w.TileSnapshot(
            org_id=org,
            tile_id=tile.id,
            query_run_ids=snap.query_run_ids,
            as_of=as_of,
            summary=snap.summary,
            status=snap.status,
            error=snap.error,
        )
        session.add(row)
        await session.flush()
        current = await session.get(w.Tile, tile.id, with_for_update=True)
        if current is not None and current.deleted_at is None:
            current.latest_snapshot_id = row.id
            current.stale = False


def _ids(payload: dict[str, Any]) -> list[uuid.UUID]:
    return [uuid.UUID(str(t)) for t in payload.get("tile_ids") or []]


async def tile_run_requested(event: Event) -> None:
    p = event.payload
    await run_tiles(
        event.org_id,
        uuid.UUID(str(p["workspace_id"])),
        _ids(p),
        uuid.UUID(str(p["requested_by"])),
        None,
    )


async def refresh_requested(event: Event) -> None:
    p = event.payload
    workspace_id = uuid.UUID(str(p["workspace_id"]))
    asker = uuid.UUID(str(p["requested_by"]))
    as_of = datetime.date.fromisoformat(str(p["as_of"])) if p.get("as_of") else None
    done = await run_tiles(event.org_id, workspace_id, _ids(p), asker, as_of)
    async with scoped_session(ROLE, event.org_id, asker) as session:
        item = await session.get(w.Workspace, workspace_id, with_for_update=True)
        if item is not None:
            item.last_refreshed_at = datetime.datetime.now(datetime.UTC)
    failed = sum(1 for s in done.values() if s.status == "failed")
    logger.info("workspace %s refreshed: %d tiles, %d failed", workspace_id, len(done), failed)


HANDLERS: dict[str, Handler] = {TILE_RUN: tile_run_requested, REFRESH: refresh_requested}
