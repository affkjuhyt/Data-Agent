"""Phiên làm việc (API-03): workspaces, tiles, shares, participants, comments, session reports.

Every route works inside the caller's Data Space (X-Data-Space). A workspace the caller cannot
see answers 404, the same as one that does not exist (A1.7); one it sees with too weak a level
answers 403. Running tiles, the four report checks and notifications are other blocks' work: the
routes record the request in workspace.outbox, in the same transaction (D1 rule 8).
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Collection, Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import ColumnElement, and_, any_, exists, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from libs.dataagent_db.models import query as query_models
from libs.dataagent_db.models import workspace as ws
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.org import capabilities_of
from services.api.modules.workspace import rules
from services.api.modules.workspace.report_storage import REPORT_BUCKET, ReportStore, report_store
from services.api.modules.workspace.schemas import (
    Accepted,
    CommentCreate,
    CommentPatch,
    CommentView,
    HiddenReason,
    HiddenTile,
    ParticipantCreate,
    ParticipantView,
    PublishRequest,
    RecipientPreview,
    RefreshRequest,
    ReportCreate,
    ReportDocumentView,
    ReportView,
    ShareGrant,
    SharesPut,
    ShareView,
    TileCreate,
    TilePatch,
    TileView,
    WorkspaceCreate,
    WorkspaceDetail,
    WorkspacePatch,
    WorkspaceView,
)

router = APIRouter()
ROLE = "svc_workspace"

PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 1_000


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        ws.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=jsonable_encoder(payload),
        )
    )


def _invalid(field: str, detail: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        detail,
        errors=[FieldError(field=field, code="invalid", message=detail)],
    )


async def _permission(
    session: AsyncSession, principal: Principal, item: ws.Workspace
) -> str | None:
    if item.owner_id == principal.user_id:
        return "owner"
    share = await session.scalar(
        select(ws.WorkspaceShare).where(
            ws.WorkspaceShare.workspace_id == item.id,
            ws.WorkspaceShare.principal_type == "user",
            ws.WorkspaceShare.principal_id == principal.user_id,
        )
    )
    role = None
    if share is None:
        role = await session.scalar(
            select(ws.WsParticipant.role).where(
                ws.WsParticipant.workspace_id == item.id,
                ws.WsParticipant.principal_type == "user",
                ws.WsParticipant.principal_id == principal.user_id,
            )
        )
    return rules.permission_of(
        is_owner=False,
        share_permission=share.permission if share else None,
        share_expires_at=share.expires_at if share else None,
        participant_role=role,
        now=_now(),
    )


async def _open(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    workspace_id: uuid.UUID,
    needed: str,
    *,
    lock: bool = False,
) -> tuple[ws.Workspace, str]:
    """The workspace and the caller's level on it; 404 when out of sight, 403 when too weak.
    `lock` takes the row (FOR UPDATE) for writes that keep tile_count or versions."""
    query = select(ws.Workspace).where(
        ws.Workspace.id == workspace_id,
        ws.Workspace.org_id == principal.org_id,
        ws.Workspace.data_space_id == space,
        ws.Workspace.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    item = await session.scalar(query)
    permission = await _permission(session, principal, item) if item is not None else None
    if item is None or permission is None:
        raise Problem(404, "not_found", "Workspace not found.")
    if not rules.at_least(permission, needed):
        raise Problem(403, "forbidden", f"This needs the {needed} permission on the workspace.")
    return item, permission


def _visible(principal: Principal, space: uuid.UUID) -> ColumnElement[bool]:
    """Workspaces of the Data Space the caller holds any level on, as one SQL condition."""
    me = principal.user_id
    share = ws.WorkspaceShare
    mine = and_(
        share.workspace_id == ws.Workspace.id,
        share.principal_type == "user",
        share.principal_id == me,
    )
    live_share = exists().where(
        mine, or_(share.expires_at.is_(None), share.expires_at > func.now())
    )
    seat = exists().where(
        ws.WsParticipant.workspace_id == ws.Workspace.id,
        ws.WsParticipant.principal_type == "user",
        ws.WsParticipant.principal_id == me,
        ws.WsParticipant.role.in_(tuple(rules.ROLE_PERMISSION)),
    )
    # Same rule as rules.permission_of: a user share, when there is one, decides (BR-89).
    return and_(
        ws.Workspace.org_id == principal.org_id,
        ws.Workspace.data_space_id == space,
        ws.Workspace.deleted_at.is_(None),
        or_(ws.Workspace.owner_id == me, live_share, and_(seat, ~exists().where(mine))),
    )


def _writable(item: ws.Workspace) -> None:
    if item.status != "active":
        raise Problem(409, "invalid_state", "The workspace is archived; reactivate it first.")


async def _tile(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    tile_id: uuid.UUID,
    needed: str,
    *,
    lock: bool = False,
) -> tuple[ws.Tile, ws.Workspace]:
    tile = await session.scalar(
        select(ws.Tile).where(
            ws.Tile.id == tile_id,
            ws.Tile.org_id == principal.org_id,
            ws.Tile.deleted_at.is_(None),
        )
    )
    if tile is None:
        raise Problem(404, "not_found", "Tile not found.")
    try:
        item, _ = await _open(session, principal, space, tile.workspace_id, needed, lock=lock)
    except Problem as problem:
        if problem.status == 404:
            raise Problem(404, "not_found", "Tile not found.") from None
        raise
    return tile, item


async def _tiles(session: AsyncSession, workspace_id: uuid.UUID) -> list[ws.Tile]:
    rows = await session.scalars(
        select(ws.Tile)
        .where(ws.Tile.workspace_id == workspace_id, ws.Tile.deleted_at.is_(None))
        .order_by(ws.Tile.position, ws.Tile.id)
    )
    return list(rows.all())


def _order(tiles: list[ws.Tile]) -> list[uuid.UUID]:
    try:
        return rules.topological_order([(t.id, t.position, t.depends_on) for t in tiles])
    except rules.CycleError:
        raise Problem(
            422, "business_rule_violated", "The tile dependencies form a cycle."
        ) from None


def _accepted(response: Response, location: str, accepted: Accepted) -> Accepted:
    response.headers["Location"] = location
    return accepted


# --- workspaces ---------------------------------------------------------------------------------


@router.get(
    "/v1/workspaces",
    response_model=Page[WorkspaceView],
    **endpoint("GET", "/v1/workspaces", operation_id="listWorkspaces"),
)
async def list_workspaces(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "title", "status", default="updated_at desc")),
    ],
    owner: Annotated[
        uuid.UUID | Literal["me"] | None,
        Query(description="owner_id, or `me` for the caller's own workspaces"),
    ] = None,
    status: Literal["active", "archived"] | None = None,
) -> Page[WorkspaceView]:
    me = principal.user_id
    query = select(ws.Workspace).where(_visible(principal, space))
    if owner is not None:
        query = query.where(ws.Workspace.owner_id == (me if owner == "me" else owner))
    if status is not None:
        query = query.where(ws.Workspace.status == status)
    w = ws.Workspace
    query = keyset(
        query,
        columns={
            "created_at": w.created_at,
            "updated_at": w.updated_at,
            "title": w.title,
            "status": w.status,
        },
        tiebreak=w.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[WorkspaceView.model_validate(row) for row in rows], next_page_token=token)


@router.post(
    "/v1/workspaces",
    status_code=201,
    response_model=WorkspaceView,
    **endpoint("POST", "/v1/workspaces", operation_id="createWorkspace"),
)
async def create_workspace(
    body: WorkspaceCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> WorkspaceView:
    async with transaction(ROLE, principal) as session:
        item = ws.Workspace(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            owner_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(item)
        await session.flush()
        # UC-02-01: the creator is the owner in the roster.
        session.add(
            ws.WsParticipant(
                id=uuid7(),
                org_id=principal.org_id,
                workspace_id=item.id,
                principal_type="user",
                principal_id=principal.user_id,
                role="owner",
            )
        )
        await session.flush()
        await session.refresh(item)
        return WorkspaceView.model_validate(item)


@router.get(
    "/v1/workspaces/{id}",
    response_model=WorkspaceDetail,
    **endpoint("GET", "/v1/workspaces/{id}", operation_id="getWorkspace"),
)
async def get_workspace(
    workspace_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> WorkspaceDetail:
    async with transaction(ROLE, principal) as session:
        item, permission = await _open(session, principal, space, workspace_id, "view")
        tiles = await _tiles(session, item.id)
        participants = (
            await session.scalars(
                select(ws.WsParticipant)
                .where(ws.WsParticipant.workspace_id == item.id)
                .order_by(ws.WsParticipant.joined_at, ws.WsParticipant.id)
            )
        ).all()
        return WorkspaceDetail.model_validate(
            {
                **WorkspaceView.model_validate(item).model_dump(),
                "tiles": [TileView.model_validate(t) for t in tiles],
                "participants": [ParticipantView.model_validate(p) for p in participants],
                "permission": permission,
            }
        )


@router.patch(
    "/v1/workspaces/{id}",
    response_model=WorkspaceView,
    **endpoint("PATCH", "/v1/workspaces/{id}", operation_id="patchWorkspace"),
)
async def patch_workspace(
    body: WorkspacePatch,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> WorkspaceView:
    changes = body.model_dump(exclude_unset=True)
    for field in ("title", "params", "status"):
        if field in changes and changes[field] is None:
            raise _invalid(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "edit", lock=True)
        status = changes.pop("status", item.status)
        if changes and item.status != "active" and status != "active":
            raise Problem(409, "invalid_state", "An archived workspace cannot be edited.")
        for key, value in changes.items():
            setattr(item, key, value)
        if "params" in changes:
            # UC-02-01 step 4: session parameters apply to every tile, so each needs a rerun.
            await session.execute(
                update(ws.Tile)
                .where(ws.Tile.workspace_id == item.id, ws.Tile.deleted_at.is_(None))
                .values(stale=True, updated_at=_now())
            )
        if status != item.status:
            item.status = status
            _emit(
                session,
                principal,
                "workspace",
                item.id,
                "workspace.status_changed",
                workspace_id=item.id,
                data_space_id=item.data_space_id,
                status=status,
            )
        item.updated_at = _now()
        item.updated_by = principal.user_id
        await session.flush()
        return WorkspaceView.model_validate(item)


# --- tiles --------------------------------------------------------------------------------------


@router.post(
    "/v1/workspaces/{id}/tiles",
    status_code=201,
    response_model=TileView,
    **endpoint("POST", "/v1/workspaces/{id}/tiles", operation_id="createTile"),
)
async def create_tile(
    body: TileCreate,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> TileView:
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "edit", lock=True)
        _writable(item)
        known = {t.id for t in await _tiles(session, item.id)}
        # BR-06: a dependency outside this workspace (so outside its Data Space) is refused.
        if set(body.depends_on) - known:
            raise _invalid("depends_on", "depends_on names a tile that is not in this workspace.")
        # Positions are unique over every row of the workspace, soft-deleted ones included
        # (tile_workspace_id_position_key), so the shifts below move all of them.
        if body.position is None:
            last = await session.scalar(
                select(func.max(ws.Tile.position)).where(ws.Tile.workspace_id == item.id)
            )
            position = 0 if last is None else last + 1
        else:
            position = body.position
            await session.execute(
                update(ws.Tile)
                .where(ws.Tile.workspace_id == item.id, ws.Tile.position >= position)
                .values(position=ws.Tile.position + 1)
            )
        tile = ws.Tile(
            id=uuid7(),
            org_id=principal.org_id,
            workspace_id=item.id,
            position=position,
            kind=body.kind,
            title=body.title,
            spec=body.spec,
            depends_on=list(dict.fromkeys(body.depends_on)),
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(tile)
        # The CHECK tile_count BETWEEN 0 AND 200 is BR-87's limit; the row lock above keeps
        # the count exact under concurrent adds.
        item.tile_count = item.tile_count + 1
        item.updated_at = _now()
        item.updated_by = principal.user_id
        await session.flush()
        await session.refresh(tile)
        return TileView.model_validate(tile)


@router.patch(
    "/v1/tiles/{id}",
    response_model=TileView,
    **endpoint("PATCH", "/v1/tiles/{id}", operation_id="patchTile"),
)
async def patch_tile(
    body: TilePatch,
    tile_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> TileView:
    # An explicit null is "no change", except for the title, which may be cleared.
    changes = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None or key == "title"
    }
    decision = changes.pop("agent_suggestion_state", None)
    async with transaction(ROLE, principal) as session:
        tile, item = await _tile(session, principal, space, tile_id, "edit", lock=True)
        _writable(item)
        tiles = await _tiles(session, item.id)
        graph = {t.id: list(t.depends_on) for t in tiles}
        now = _now()
        if decision is not None and tile.agent_suggestion_state != "suggested":
            raise Problem(
                409,
                "invalid_state",
                "Only a tile the agent suggested can be accepted or dismissed.",
            )
        if "depends_on" in changes:
            wanted = list(dict.fromkeys(changes["depends_on"]))
            if set(wanted) - set(graph) - {tile.id}:
                raise _invalid(
                    "depends_on", "depends_on names a tile that is not in this workspace."
                )
            if rules.creates_cycle(graph, tile.id, wanted):
                raise Problem(
                    422, "business_rule_violated", "The tile dependencies would form a cycle."
                )
            changes["depends_on"] = wanted
            graph[tile.id] = wanted
        if "position" in changes and changes["position"] != tile.position:
            # Move: the tiles between the old and new place close up (the unique key is deferred).
            old, new = tile.position, changes["position"]
            shift = (
                and_(ws.Tile.position >= new, ws.Tile.position < old)
                if new < old
                else and_(ws.Tile.position > old, ws.Tile.position <= new)
            )
            await session.execute(
                update(ws.Tile)
                .where(ws.Tile.workspace_id == item.id, ws.Tile.id != tile.id, shift)
                .values(position=ws.Tile.position + (1 if new < old else -1))
            )
        for key, value in changes.items():
            setattr(tile, key, value)
        if "spec" in changes or "depends_on" in changes:
            # UC-02-06 step 4: the edited tile and every tile downstream of it need a rerun.
            tile.stale = True
            downstream = rules.dependents(graph, tile.id)
            if downstream:
                await session.execute(
                    update(ws.Tile)
                    .where(ws.Tile.id.in_(downstream))
                    .values(stale=True, updated_at=now)
                )
        if decision is not None:
            tile.agent_suggestion_state = decision
            if decision == "dismissed":
                # AC-02-06-03: a dismissed suggestion leaves no tile behind.
                tile.deleted_at = now
                item.tile_count = item.tile_count - 1
            _emit(
                session,
                principal,
                "tile",
                tile.id,
                "tile.suggestion_decided",
                tile_id=tile.id,
                workspace_id=item.id,
                agent_suggestion_state=decision,
            )
        tile.updated_at = now
        tile.updated_by = principal.user_id
        item.updated_at = now
        item.updated_by = principal.user_id
        await session.flush()
        await session.refresh(tile)
        return TileView.model_validate(tile)


@router.post(
    "/v1/tiles/{id}:run",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/tiles/{id}:run", operation_id="runTile"),
)
async def run_tile(
    tile_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        tile, item = await _tile(session, principal, space, tile_id, "view")
        _writable(item)
        tiles = await _tiles(session, item.id)
        downstream = rules.dependents({t.id: list(t.depends_on) for t in tiles}, tile.id)
        # UC-02-02 1a: the tile, then the tiles that depend on it, in dependency order.
        order = _order([t for t in tiles if t.id == tile.id or t.id in downstream])
        _emit(
            session,
            principal,
            "tile",
            tile.id,
            "tile.run_requested",
            tile_id=tile.id,
            workspace_id=item.id,
            data_space_id=item.data_space_id,
            # BR-07: queries run with the rights of whoever asked, not the owner's.
            requested_by=principal.user_id,
            tile_ids=order,
        )
    # The snapshot appears as the tile's latest_snapshot_id on the workspace.
    return _accepted(
        response,
        f"/v1/workspaces/{item.id}",
        Accepted(id=tile.id, status="queued", poll_after_ms=POLL_AFTER_MS),
    )


@router.post(
    "/v1/workspaces/{id}:refresh",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/workspaces/{id}:refresh", operation_id="refreshWorkspace"),
)
async def refresh_workspace(
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    body: RefreshRequest = RefreshRequest(),
) -> Accepted:
    as_of = body.as_of
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "view")
        _writable(item)
        tiles = await _tiles(session, item.id)
        if not tiles:
            raise Problem(409, "invalid_state", "The workspace has no tile to refresh.")
        _emit(
            session,
            principal,
            "workspace",
            item.id,
            "workspace.refresh_requested",
            workspace_id=item.id,
            data_space_id=item.data_space_id,
            requested_by=principal.user_id,
            as_of=as_of,
            # UC-02-02 step 2: roots first; the worker skips the dependents of a failed tile.
            tile_ids=_order(tiles),
        )
    return _accepted(
        response,
        f"/v1/workspaces/{item.id}",
        Accepted(id=item.id, status="queued", poll_after_ms=POLL_AFTER_MS),
    )


# --- sharing and participants -------------------------------------------------------------------

_SEAT_FOR = {"view": "viewer", "comment": "viewer", "edit": "editor"}


async def _seat(
    session: AsyncSession,
    principal: Principal,
    item: ws.Workspace,
    principal_id: uuid.UUID,
    role: str | None,
) -> None:
    """Keep the roster row that mirrors a user share (UC-02-03 postcondition); None removes it.
    The owner's seat is never touched."""
    seat = await session.scalar(
        select(ws.WsParticipant).where(
            ws.WsParticipant.workspace_id == item.id,
            ws.WsParticipant.principal_type == "user",
            ws.WsParticipant.principal_id == principal_id,
        )
    )
    if seat is not None and seat.role == "owner":
        return
    if role is None:
        if seat is not None:
            await session.delete(seat)
    elif seat is None:
        session.add(
            ws.WsParticipant(
                id=uuid7(),
                org_id=principal.org_id,
                workspace_id=item.id,
                principal_type="user",
                principal_id=principal_id,
                role=role,
            )
        )
    else:
        seat.role = role


@router.put(
    "/v1/workspaces/{id}/shares",
    response_model=Page[ShareView],
    **endpoint("PUT", "/v1/workspaces/{id}/shares", operation_id="shareWorkspace"),
)
# UC-02-03: the owner shares the workspace. An editor deciding who gets in could keep or extend
# their own access past its expiry (BR-89) or remove other people's.
async def share_workspace(
    body: SharesPut,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> Page[ShareView]:
    now = _now()
    for index, grant in enumerate(body.shares):
        if grant.expires_at is not None and grant.expires_at <= now:
            raise _invalid(f"shares.{index}.expires_at", "expires_at must be in the future.")
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "owner", lock=True)
        for index, grant in enumerate(body.shares):
            if grant.principal_type == "user" and grant.principal_id == item.owner_id:
                raise _invalid(
                    f"shares.{index}.principal_id", "The owner already holds the workspace."
                )
        current = {
            (s.principal_type, s.principal_id): s
            for s in (
                await session.scalars(
                    select(ws.WorkspaceShare).where(ws.WorkspaceShare.workspace_id == item.id)
                )
            ).all()
        }
        wanted = {(g.principal_type, g.principal_id): g for g in body.shares}
        for key, share in current.items():
            if key in wanted:
                continue
            await session.delete(share)
            if share.principal_type == "user":
                await _seat(session, principal, item, share.principal_id, None)
            _emit(
                session,
                principal,
                "workspace_share",
                share.id,
                "workspace_share.revoked",
                workspace_id=item.id,
                principal_type=share.principal_type,
                principal_id=share.principal_id,
            )
        for key, grant in wanted.items():
            existing = current.get(key)
            if existing is not None and (existing.permission, existing.expires_at) == (
                grant.permission,
                grant.expires_at,
            ):
                continue
            if existing is not None:
                granted = existing
            else:
                granted = ws.WorkspaceShare(
                    id=uuid7(),
                    org_id=principal.org_id,
                    workspace_id=item.id,
                    principal_type=grant.principal_type,
                    principal_id=grant.principal_id,
                    granted_by=principal.user_id,
                    created_by=principal.user_id,
                )
                session.add(granted)
            granted.permission = grant.permission
            granted.expires_at = grant.expires_at
            granted.granted_by = principal.user_id
            granted.updated_by = principal.user_id
            granted.updated_at = now
            if grant.principal_type == "user":
                await _seat(
                    session, principal, item, grant.principal_id, _SEAT_FOR[grant.permission]
                )
            # Notification and audit (UC-02-03 step 3) belong to their blocks.
            _emit(
                session,
                principal,
                "workspace_share",
                granted.id,
                "workspace_share.granted",
                workspace_id=item.id,
                principal_type=grant.principal_type,
                principal_id=grant.principal_id,
                permission=grant.permission,
                expires_at=grant.expires_at,
            )
        await session.flush()
        shares = (
            await session.scalars(
                select(ws.WorkspaceShare)
                .where(ws.WorkspaceShare.workspace_id == item.id)
                .order_by(ws.WorkspaceShare.created_at, ws.WorkspaceShare.id)
            )
        ).all()
        for share in shares:
            await session.refresh(share)
        return Page(items=await _share_views(session, principal, shares))


@router.delete(
    "/v1/workspaces/{id}/shares/{principal_id}",
    status_code=204,
    **endpoint(
        "DELETE", "/v1/workspaces/{id}/shares/{principal_id}", operation_id="revokeWorkspaceShare"
    ),
)
async def revoke_workspace_share(
    workspace_id: PathId,
    principal_id: uuid.UUID,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> Response:
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "owner", lock=True)
        shares = (
            await session.scalars(
                select(ws.WorkspaceShare).where(
                    ws.WorkspaceShare.workspace_id == item.id,
                    ws.WorkspaceShare.principal_id == principal_id,
                )
            )
        ).all()
        if not shares:
            raise Problem(404, "not_found", "Workspace share not found.")
        for share in shares:
            await session.delete(share)
            if share.principal_type == "user":
                await _seat(session, principal, item, share.principal_id, None)
            _emit(
                session,
                principal,
                "workspace_share",
                share.id,
                "workspace_share.revoked",
                workspace_id=item.id,
                principal_type=share.principal_type,
                principal_id=share.principal_id,
            )
    return Response(status_code=204)


@router.get(
    "/v1/workspaces/{id}/shares",
    response_model=Page[ShareView],
    **endpoint("GET", "/v1/workspaces/{id}/shares", operation_id="listWorkspaceShares"),
)
async def list_workspace_shares(
    workspace_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> Page[ShareView]:
    # A1.3: the set PUT replaces is readable by whoever sees the workspace, expired grants
    # included (expires_at says so), so the share dialog starts from what is stored.
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "view")
        shares = (
            await session.scalars(
                select(ws.WorkspaceShare)
                .where(ws.WorkspaceShare.workspace_id == item.id)
                .order_by(ws.WorkspaceShare.created_at, ws.WorkspaceShare.id)
            )
        ).all()
        return Page(items=await _share_views(session, principal, shares))


async def _names(
    session: AsyncSession,
    principal: Principal,
    users: Collection[uuid.UUID],
    groups: Collection[uuid.UUID] = (),
) -> dict[tuple[str, uuid.UUID], str]:
    """Display names of the organisation's users and groups, keyed by (principal_type, id)."""
    out: dict[tuple[str, uuid.UUID], str] = {}
    if users:
        found = await session.execute(
            select(org.AppUser.id, org.AppUser.display_name).where(
                org.AppUser.id.in_(users), org.AppUser.org_id == principal.org_id
            )
        )
        out |= {("user", i): n for i, n in found.tuples()}
    if groups:
        found = await session.execute(
            select(org.UserGroup.id, org.UserGroup.name).where(
                org.UserGroup.id.in_(groups), org.UserGroup.org_id == principal.org_id
            )
        )
        out |= {("group", i): n for i, n in found.tuples()}
    return out


async def _share_views(
    session: AsyncSession, principal: Principal, shares: Sequence[ws.WorkspaceShare]
) -> list[ShareView]:
    names = await _names(
        session,
        principal,
        {s.principal_id for s in shares if s.principal_type == "user"},
        {s.principal_id for s in shares if s.principal_type == "group"},
    )
    return [
        ShareView.model_validate(s).model_copy(
            update={"principal_name": names.get((s.principal_type, s.principal_id))}
        )
        for s in shares
    ]


async def _comment_views(
    session: AsyncSession, principal: Principal, rows: Sequence[ws.WsComment]
) -> list[CommentView]:
    people = {r.author_id for r in rows} | {r.assignee_id for r in rows if r.assignee_id}
    names = await _names(session, principal, people)
    return [
        CommentView.model_validate(r).model_copy(
            update={
                "author_name": names.get(("user", r.author_id)),
                "assignee_name": names.get(("user", r.assignee_id)) if r.assignee_id else None,
            }
        )
        for r in rows
    ]


def _member_of(principal_type: str, principal_id: uuid.UUID) -> ColumnElement[bool]:
    """The data_space_member rows still valid that make this user or group a member."""
    member = org.DataSpaceMember
    if principal_type == "user":
        groups = select(org.UserGroupMember.group_id).where(
            org.UserGroupMember.user_id == principal_id
        )
        who = or_(
            and_(member.principal_type == "user", member.principal_id == principal_id),
            and_(member.principal_type == "group", member.principal_id.in_(groups)),
        )
    else:
        who = and_(member.principal_type == "group", member.principal_id == principal_id)
    return and_(who, or_(member.valid_until.is_(None), member.valid_until > func.now()))


async def _recipient(
    session: AsyncSession,
    principal: Principal,
    grant: ShareGrant,
    home: uuid.UUID,
    others: set[uuid.UUID],
) -> tuple[HiddenReason | None, bool, set[uuid.UUID]]:
    """(why the recipient cannot open the session or None, row scope on its membership,
    the other Data Spaces it can read) for one share not saved yet."""
    member = org.DataSpaceMember
    if grant.principal_type == "user":
        known = await session.scalar(
            select(org.AppUser.id).where(
                org.AppUser.id == grant.principal_id,
                org.AppUser.org_id == principal.org_id,
                org.AppUser.status == "active",
            )
        )
        if known is None:
            return "not_in_organisation", False, set()
        # The org module resolves capabilities the way a signed-in request would (org-wide
        # roles, group mappings, Data Space roles, expiries), so the preview cannot drift from it.
        capabilities = await capabilities_of(principal.org_id, grant.principal_id, home)
        if capabilities is None:
            return "not_data_space_member", False, set()
        if "ask" not in capabilities:
            return "missing_capability", False, set()
        readable = {
            other
            for other in others
            if await capabilities_of(principal.org_id, grant.principal_id, other) is not None
        }
    else:
        known = await session.scalar(
            select(org.UserGroup.id).where(
                org.UserGroup.id == grant.principal_id,
                org.UserGroup.org_id == principal.org_id,
            )
        )
        if known is None:
            return "not_in_organisation", False, set()
        # A group is judged on its own memberships: what each person also holds alone does
        # not widen what the share gives the group as a whole.
        roles = (
            await session.scalars(
                select(member.role_id).where(
                    member.data_space_id == home,
                    _member_of("group", grant.principal_id),
                )
            )
        ).all()
        if not roles:
            return "not_data_space_member", False, set()
        asks = await session.scalar(
            select(func.count())
            .select_from(org.Role)
            .where(org.Role.id.in_(roles), literal("ask") == any_(org.Role.capabilities))
        )
        if not asks:
            return "missing_capability", False, set()
        readable = set(
            (
                await session.scalars(
                    select(member.data_space_id).where(
                        member.data_space_id.in_(others),
                        _member_of("group", grant.principal_id),
                    )
                )
            ).all()
        )
    limited = await session.scalar(
        select(
            exists().where(
                member.data_space_id == home,
                member.row_scope.is_not(None),
                _member_of(grant.principal_type, grant.principal_id),
            )
        )
    )
    return None, bool(limited), readable


@router.post(
    "/v1/workspaces/{id}/shares:preview",
    response_model=Page[RecipientPreview],
    **endpoint("POST", "/v1/workspaces/{id}/shares:preview", operation_id="previewWorkspaceShares"),
)
# UC-02-03 step 4 before step 3: what each recipient would see, from its own rights (BR-07,
# BR-89). Only the owner shares, so only the owner previews; nothing is written.
async def preview_workspace_shares(
    body: SharesPut,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> Page[RecipientPreview]:
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "owner")
        tiles = await _tiles(session, item.id)
        # A tile reads the Data Spaces its last result's query runs read (tile_snapshot →
        # query_run); a tile never run reads only the workspace's own.
        snapshots = {
            s.tile_id: list(s.query_run_ids or [])
            for s in (
                await session.scalars(
                    select(ws.TileSnapshot).where(
                        ws.TileSnapshot.id.in_(
                            [t.latest_snapshot_id for t in tiles if t.latest_snapshot_id]
                        )
                    )
                )
            ).all()
        }
        run_ids = {r for runs in snapshots.values() for r in runs}
        run_space: dict[uuid.UUID, uuid.UUID] = {}
        if run_ids:
            qr = query_models.QueryRun
            found = await session.execute(
                select(qr.id, qr.data_space_id).where(
                    qr.id.in_(run_ids), qr.org_id == principal.org_id
                )
            )
            run_space = {row.id: row.data_space_id for row in found}
        reads = {
            t.id: {run_space[r] for r in snapshots.get(t.id, []) if r in run_space} - {space}
            for t in tiles
        }
        others = set().union(*reads.values()) if reads else set()
        previews = []
        for grant in body.shares:
            reason, limited, readable = await _recipient(session, principal, grant, space, others)
            if reason is not None:
                visible, hidden = [], [HiddenTile(tile_id=t.id, reason=reason) for t in tiles]
            else:
                visible = [t.id for t in tiles if reads[t.id] <= readable]
                hidden = [
                    HiddenTile(tile_id=t.id, reason="other_data_space")
                    for t in tiles
                    if not reads[t.id] <= readable
                ]
            previews.append(
                RecipientPreview(
                    principal_type=grant.principal_type,
                    principal_id=grant.principal_id,
                    permission=grant.permission,
                    can_open=reason is None,
                    reason=reason,
                    row_scope_limited=limited,
                    visible_tile_ids=visible,
                    hidden_tiles=hidden,
                )
            )
    return Page(items=previews)


@router.post(
    "/v1/workspaces/{id}/participants",
    status_code=201,
    response_model=ParticipantView,
    **endpoint("POST", "/v1/workspaces/{id}/participants", operation_id="addWorkspaceParticipant"),
)
async def add_participant(
    body: ParticipantCreate,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> ParticipantView:
    if body.role == "owner":
        raise Problem(422, "business_rule_violated", "A workspace has one owner, its creator.")
    if (body.principal_type == "agent") != (body.role == "agent_helper"):
        raise _invalid("role", "An agent joins as agent_helper; a person as editor or viewer.")
    async with transaction(ROLE, principal) as session:
        item, _ = await _open(session, principal, space, workspace_id, "owner")
        _writable(item)
        participant = ws.WsParticipant(
            id=uuid7(),
            org_id=principal.org_id,
            workspace_id=item.id,
            **body.model_dump(),
        )
        session.add(participant)
        _emit(
            session,
            principal,
            "ws_participant",
            participant.id,
            "ws_participant.added",
            workspace_id=item.id,
            data_space_id=item.data_space_id,
            principal_type=body.principal_type,
            principal_id=body.principal_id,
            role=body.role,
            invited_by=principal.user_id,
        )
        await session.flush()
        await session.refresh(participant)
        return ParticipantView.model_validate(participant)


# --- comments -----------------------------------------------------------------------------------


@router.post(
    "/v1/tiles/{id}/comments",
    response_model=CommentView,
    status_code=201,
    **endpoint("POST", "/v1/tiles/{id}/comments", operation_id="createTileComment"),
)
async def create_comment(
    body: CommentCreate,
    tile_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> CommentView:
    async with transaction(ROLE, principal) as session:
        tile, item = await _tile(session, principal, space, tile_id, "comment")
        _writable(item)
        if body.parent_id is not None:
            parent = await session.scalar(
                select(ws.WsComment.id).where(
                    ws.WsComment.id == body.parent_id,
                    ws.WsComment.workspace_id == item.id,
                    ws.WsComment.deleted_at.is_(None),
                )
            )
            if parent is None:
                raise _invalid("parent_id", "parent_id is not a comment of this workspace.")
        mentions = list(dict.fromkeys(body.mentions))
        agents = (
            (
                await session.scalars(
                    select(ws.WsParticipant.principal_id).where(
                        ws.WsParticipant.workspace_id == item.id,
                        ws.WsParticipant.principal_type == "agent",
                        ws.WsParticipant.principal_id.in_(mentions),
                    )
                )
            ).all()
            if mentions
            else []
        )
        comment = ws.WsComment(
            id=uuid7(),
            org_id=principal.org_id,
            workspace_id=item.id,
            tile_id=tile.id,
            parent_id=body.parent_id,
            author_id=principal.user_id,
            body=body.body,
            mentions=mentions,
            assignee_id=body.assignee_id,
            due_at=body.due_at,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(comment)
        # Notifications to the people mentioned or assigned, and (UC-02-05 1a) one agent run
        # per agent of the workspace mentioned, are the notification and agent blocks' work.
        _emit(
            session,
            principal,
            "ws_comment",
            comment.id,
            "ws_comment.created",
            comment_id=comment.id,
            workspace_id=item.id,
            tile_id=tile.id,
            parent_id=body.parent_id,
            author_id=principal.user_id,
            mentions=mentions,
            agent_mentions=list(agents),
            assignee_id=body.assignee_id,
            due_at=body.due_at,
        )
        await session.flush()
        await session.refresh(comment)
        return (await _comment_views(session, principal, [comment]))[0]


@router.get(
    "/v1/tiles/{id}/comments",
    response_model=Page[CommentView],
    **endpoint("GET", "/v1/tiles/{id}/comments", operation_id="listTileComments"),
)
async def list_tile_comments(
    tile_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order, Depends(order_by("created_at", "updated_at", default="created_at asc"))
    ],
) -> Page[CommentView]:
    # UC-02-05: the thread in the order it was written, replies (parent_id) included, and the
    # tasks given in it (assignee_id, due_at, status) on the same rows.
    c = ws.WsComment
    async with transaction(ROLE, principal) as session:
        tile, _ = await _tile(session, principal, space, tile_id, "view")
        query = keyset(
            select(c).where(c.tile_id == tile.id, c.deleted_at.is_(None)),
            columns={"created_at": c.created_at, "updated_at": c.updated_at},
            tiebreak=c.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        return Page(items=await _comment_views(session, principal, rows), next_page_token=token)


@router.patch(
    "/v1/comments/{id}",
    response_model=CommentView,
    **endpoint("PATCH", "/v1/comments/{id}", operation_id="patchComment"),
)
async def patch_comment(
    body: CommentPatch,
    comment_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> CommentView:
    changes = body.model_dump(exclude_unset=True)
    for field in ("body", "status"):
        if field in changes and changes[field] is None:
            raise _invalid(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        comment = await session.scalar(
            select(ws.WsComment).where(
                ws.WsComment.id == comment_id,
                ws.WsComment.org_id == principal.org_id,
                ws.WsComment.deleted_at.is_(None),
            )
        )
        if comment is None:
            raise Problem(404, "not_found", "Comment not found.")
        try:
            item, _ = await _open(session, principal, space, comment.workspace_id, "view")
        except Problem as problem:
            if problem.status == 404:
                raise Problem(404, "not_found", "Comment not found.") from None
            raise
        is_author = comment.author_id == principal.user_id
        if not is_author and comment.assignee_id != principal.user_id:
            raise Problem(403, "forbidden", "Only the author or the assignee changes a comment.")
        if "body" in changes and not is_author:
            raise Problem(403, "forbidden", "Only the author edits the text of a comment.")
        now = _now()
        status = changes.pop("status", comment.status)
        for key, value in changes.items():
            setattr(comment, key, value)
        if status != comment.status:
            comment.status = status
            # The CHECK holds status = 'done' to a resolved_at.
            comment.resolved_at = now if status == "done" else None
            _emit(
                session,
                principal,
                "ws_comment",
                comment.id,
                "ws_comment.status_changed",
                comment_id=comment.id,
                workspace_id=item.id,
                status=status,
                author_id=comment.author_id,
                assignee_id=comment.assignee_id,
                changed_by=principal.user_id,
            )
        comment.updated_at = now
        comment.updated_by = principal.user_id
        await session.flush()
        return (await _comment_views(session, principal, [comment]))[0]


# --- session reports ----------------------------------------------------------------------------


async def _report(
    session: AsyncSession, principal: Principal, report_id: uuid.UUID
) -> ws.SessionReport:
    report = await session.scalar(
        select(ws.SessionReport)
        .where(ws.SessionReport.id == report_id, ws.SessionReport.org_id == principal.org_id)
        .with_for_update()
    )
    if report is None:
        raise Problem(404, "not_found", "Session report not found.")
    return report


def _report_status_changed(
    session: AsyncSession, principal: Principal, report: ws.SessionReport, **extra: Any
) -> None:
    _emit(
        session,
        principal,
        "session_report",
        report.id,
        "session_report.status_changed",
        session_report_id=report.id,
        workspace_id=report.workspace_id,
        version=report.version,
        status=report.status,
        **extra,
    )


@router.post(
    "/v1/workspaces/{id}/reports",
    response_model=ReportView,
    status_code=201,
    **endpoint("POST", "/v1/workspaces/{id}/reports", operation_id="createSessionReport"),
)
async def create_report(
    body: ReportCreate,
    workspace_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> ReportView:
    async with transaction(ROLE, principal) as session:
        # The lock makes the next version number safe under concurrent drafts.
        item, _ = await _open(session, principal, space, workspace_id, "edit", lock=True)
        tiles = [t.id for t in await _tiles(session, item.id)]
        if body.tile_ids is None:
            tile_ids = tiles
        else:
            tile_ids = list(dict.fromkeys(body.tile_ids))
            if set(tile_ids) - set(tiles):
                raise _invalid("tile_ids", "tile_ids names a tile that is not in this workspace.")
        latest = await session.scalar(
            select(func.coalesce(func.max(ws.SessionReport.version), 0)).where(
                ws.SessionReport.workspace_id == item.id
            )
        )
        version = (latest or 0) + 1
        report_id = uuid7()
        report = ws.SessionReport(
            id=report_id,
            org_id=principal.org_id,
            workspace_id=item.id,
            version=version,
            title=body.title,
            # Where the Report agent writes the draft (MinIO da-results).
            body_ref=f"session-reports/{item.id}/{report_id}/v{version}.md",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(report)
        # UC-02-07 step 1: the Report agent drafts from these tiles.
        _emit(
            session,
            principal,
            "session_report",
            report.id,
            "session_report.created",
            session_report_id=report.id,
            workspace_id=item.id,
            data_space_id=item.data_space_id,
            version=version,
            tile_ids=tile_ids,
            requested_by=principal.user_id,
        )
        await session.flush()
        await session.refresh(report)
        return ReportView.model_validate(report)


@router.post(
    "/v1/session-reports/{id}:check",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/session-reports/{id}:check", operation_id="checkSessionReport"),
)
async def check_report(
    report_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        report = await _report(session, principal, report_id)
        try:
            await _open(session, principal, space, report.workspace_id, "edit")
        except Problem as problem:
            if problem.status == 404:
                raise Problem(404, "not_found", "Session report not found.") from None
            raise
        if report.status not in ("draft", "ready", "failed_checks"):
            raise Problem(
                409, "invalid_state", f"A {report.status} report cannot be checked again."
            )
        # The four checks (grounding, filed numbers, recipients' rights, personal data) run in
        # a worker, which sets ready or failed_checks.
        report.status = "checking"
        report.checks = {}
        report.updated_at = _now()
        report.updated_by = principal.user_id
        _report_status_changed(session, principal, report, requested_by=principal.user_id)
    return _accepted(
        response,
        f"/v1/session-reports/{report.id}",
        Accepted(id=report.id, status="checking", poll_after_ms=POLL_AFTER_MS),
    )


@router.post(
    "/v1/session-reports/{id}:publish",
    response_model=ReportView,
    **endpoint("POST", "/v1/session-reports/{id}:publish", operation_id="publishSessionReport"),
)
async def publish_report(
    report_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    body: PublishRequest = PublishRequest(),
) -> ReportView:
    recipients = body.recipients
    async with transaction(ROLE, principal) as session:
        report = await _report(session, principal, report_id)
        # The approver acts for the Data Space (capability approve, checked by A2), so a share of
        # the workspace is not needed; the report must still be in the caller's Data Space.
        in_space = await session.scalar(
            select(ws.Workspace.id).where(
                ws.Workspace.id == report.workspace_id,
                ws.Workspace.data_space_id == space,
                ws.Workspace.deleted_at.is_(None),
            )
        )
        if in_space is None:
            raise Problem(404, "not_found", "Session report not found.")
        if report.status not in ("ready", "pending_approval"):
            raise Problem(409, "invalid_state", "The report has not passed the four checks.")
        failed = rules.failed_checks(report.checks)
        if failed:
            raise Problem(
                422,
                "business_rule_violated",
                f"Checks not passed: {', '.join(failed)}; every number needs grounding (UC-02-07).",
            )
        now = _now()
        self_approval = report.created_by == principal.user_id
        if self_approval and report.status == "pending_approval":
            raise Problem(409, "invalid_state", "The report waits for another approver.")
        if self_approval:
            # BR-53 and UC-02-07 step 4: the author does not approve its own report; the
            # review request goes to Cần bạn (the inbox block opens it from this event).
            report.status = "pending_approval"
            _report_status_changed(
                session, principal, report, requested_by=principal.user_id, recipients=recipients
            )
        else:
            report.status = "published"
            report.approved_by = principal.user_id
            report.published_at = now
            _report_status_changed(
                session, principal, report, approved_by=principal.user_id, recipients=recipients
            )
        report.updated_at = now
        report.updated_by = principal.user_id
        await session.flush()
        return ReportView.model_validate(report)


# The approver of a report acts for the Data Space (publish_report), so it reads the reports
# waiting for it, and the published ones, without a share of the workspace.
_APPROVER_SEES = ("pending_approval", "published")


def _readable_reports(principal: Principal, space: uuid.UUID) -> ColumnElement[bool]:
    in_space = select(ws.Workspace.id).where(
        ws.Workspace.org_id == principal.org_id,
        ws.Workspace.data_space_id == space,
        ws.Workspace.deleted_at.is_(None),
    )
    mine = ws.SessionReport.workspace_id.in_(
        select(ws.Workspace.id).where(_visible(principal, space))
    )
    if "approve" not in principal.capabilities:
        return mine
    return or_(
        mine,
        and_(
            ws.SessionReport.workspace_id.in_(in_space),
            ws.SessionReport.status.in_(_APPROVER_SEES),
        ),
    )


ReportStatus = Literal[
    "draft", "checking", "ready", "pending_approval", "published", "failed_checks"
]


@router.get(
    "/v1/session-reports",
    response_model=Page[ReportView],
    **endpoint("GET", "/v1/session-reports", operation_id="listSessionReports"),
)
async def list_session_reports(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "version", default="created_at desc")),
    ],
    workspace_id: uuid.UUID | None = None,
    status: ReportStatus | None = None,
) -> Page[ReportView]:
    r = ws.SessionReport
    query = select(r).where(r.org_id == principal.org_id, _readable_reports(principal, space))
    if workspace_id is not None:
        query = query.where(r.workspace_id == workspace_id)
    if status is not None:
        query = query.where(r.status == status)
    query = keyset(
        query,
        columns={"created_at": r.created_at, "updated_at": r.updated_at, "version": r.version},
        tiebreak=r.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        return Page(items=[ReportView.model_validate(row) for row in rows], next_page_token=token)


@router.get(
    "/v1/session-reports/{id}",
    response_model=ReportView,
    **endpoint("GET", "/v1/session-reports/{id}", operation_id="getSessionReport"),
)
async def get_session_report(
    report_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ReportView:
    # Where the Location of :check points: the checks object is the worker's result of the four
    # checks (grounding of each number, filed numbers, recipients' rights, personal data).
    r = ws.SessionReport
    async with transaction(ROLE, principal) as session:
        report = await session.scalar(
            select(r).where(
                r.id == report_id,
                r.org_id == principal.org_id,
                _readable_reports(principal, space),
            )
        )
        if report is None:
            raise Problem(404, "not_found", "Session report not found.")
        return ReportView.model_validate(report)


@router.get(
    "/v1/session-reports/{id}/document",
    response_model=ReportDocumentView,
    **endpoint(
        "GET",
        "/v1/session-reports/{id}/document",
        operation_id="getSessionReportDocument",
    ),
)
async def get_session_report_document(
    report_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    store: Annotated[ReportStore, Depends(report_store)],
) -> ReportDocumentView:
    r = ws.SessionReport
    async with transaction(ROLE, principal) as session:
        report = await session.scalar(
            select(r).where(
                r.id == report_id,
                r.org_id == principal.org_id,
                _readable_reports(principal, space),
            )
        )
        if report is None:
            raise Problem(404, "not_found", "Session report not found.")
        body_ref = report.body_ref
    if not body_ref or body_ref.startswith("/") or ".." in body_ref.split("/"):
        raise Problem(500, "internal_error", "The report document reference is invalid.")
    try:
        content = await store.download(REPORT_BUCKET, body_ref)
    except FileNotFoundError as exc:
        raise Problem(404, "not_found", "The report document is not available yet.") from exc
    try:
        markdown = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Problem(500, "internal_error", "The report document is not valid UTF-8.") from exc
    return ReportDocumentView(
        report_id=report.id,
        body_ref=body_ref,
        content_type="text/markdown",
        markdown=markdown,
    )
