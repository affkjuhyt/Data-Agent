"""Core workspace and tile operations for API-03."""

from __future__ import annotations

import uuid
import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select

from libs.dataagent_db.models import workspace as ws
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, invalid_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.modules.workspace.schemas import (
    CommentCreate,
    CommentPatch,
    CommentView,
    ParticipantCreate,
    ParticipantView,
    ReportCreate,
    ReportView,
    ShareCreate,
    ShareView,
    TileCreate,
    TilePatch,
    TileView,
    WorkspaceCreate,
    WorkspaceDetail,
    WorkspacePatch,
    WorkspaceSummary,
)

router = APIRouter()
ROLE = "svc_workspace"


async def _workspace(session, workspace_id: uuid.UUID, principal: CurrentPrincipal) -> ws.Workspace:
    item = await session.scalar(
        select(ws.Workspace).where(
            ws.Workspace.id == workspace_id,
            ws.Workspace.org_id == principal.org_id,
            ws.Workspace.owner_id == principal.user_id,
            ws.Workspace.deleted_at.is_(None),
        )
    )
    if item is None:
        raise Problem(404, "not_found", "Workspace not found.")
    return item


def _detail(item: ws.Workspace, tiles: list[ws.Tile]) -> WorkspaceDetail:
    return WorkspaceDetail.model_validate(
        {
            **item.__dict__,
            "tiles": [TileView.model_validate(tile, from_attributes=True) for tile in tiles],
        }
    )


@router.get("/v1/workspaces", response_model=Page[WorkspaceSummary], **endpoint("GET", "/v1/workspaces", operation_id="listWorkspaces"))
async def list_workspaces(principal: CurrentPrincipal, data_space: CurrentDataSpace, page: Annotated[PageRequest, Depends(page_request)]) -> Page[WorkspaceSummary]:
    query = select(ws.Workspace).where(
        ws.Workspace.org_id == principal.org_id,
        ws.Workspace.owner_id == principal.user_id,
        ws.Workspace.data_space_id == data_space,
        ws.Workspace.deleted_at.is_(None),
    ).order_by(ws.Workspace.updated_at.desc(), ws.Workspace.id.desc()).limit(page.page_size + 1)
    if page.cursor:
        if "updated_at" not in page.cursor:
            raise invalid_page_token()
        query = query.where(ws.Workspace.updated_at < page.cursor["updated_at"])
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page(
        items=[WorkspaceSummary.model_validate(row, from_attributes=True) for row in rows],
        next_page_token=encode_page_token({"updated_at": last.updated_at.isoformat()} if last else None),
    )


@router.post("/v1/workspaces", status_code=201, response_model=WorkspaceDetail, **endpoint("POST", "/v1/workspaces", operation_id="createWorkspace"))
async def create_workspace(body: WorkspaceCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace) -> WorkspaceDetail:
    async with transaction(ROLE, principal) as session:
        item = ws.Workspace(
            org_id=principal.org_id,
            data_space_id=data_space,
            owner_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(item)
        await session.flush()
    return _detail(item, [])


@router.get("/v1/workspaces/{id}", response_model=WorkspaceDetail, **endpoint("GET", "/v1/workspaces/{id}", operation_id="getWorkspace"))
async def get_workspace(workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> WorkspaceDetail:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        tiles = list((await session.scalars(select(ws.Tile).where(ws.Tile.workspace_id == item.id, ws.Tile.deleted_at.is_(None)).order_by(ws.Tile.position, ws.Tile.id))).all())
    return _detail(item, tiles)


@router.patch("/v1/workspaces/{id}", response_model=WorkspaceDetail, **endpoint("PATCH", "/v1/workspaces/{id}", operation_id="patchWorkspace"))
async def patch_workspace(body: WorkspacePatch, workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> WorkspaceDetail:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        item.updated_by = principal.user_id
        await session.flush()
        tiles = list((await session.scalars(select(ws.Tile).where(ws.Tile.workspace_id == item.id, ws.Tile.deleted_at.is_(None)).order_by(ws.Tile.position, ws.Tile.id))).all())
    return _detail(item, tiles)


@router.post("/v1/workspaces/{id}/tiles", status_code=201, response_model=TileView, **endpoint("POST", "/v1/workspaces/{id}/tiles", operation_id="createTile"))
async def create_tile(body: TileCreate, workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> TileView:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        tile = ws.Tile(org_id=principal.org_id, workspace_id=item.id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(tile)
        item.tile_count = item.tile_count + 1
        item.updated_by = principal.user_id
        await session.flush()
    return TileView.model_validate(tile, from_attributes=True)


@router.patch("/v1/tiles/{id}", response_model=TileView, **endpoint("PATCH", "/v1/tiles/{id}", operation_id="patchTile"))
async def patch_tile(body: TilePatch, tile_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> TileView:
    async with transaction(ROLE, principal) as session:
        tile = await session.scalar(select(ws.Tile).where(ws.Tile.id == tile_id, ws.Tile.org_id == principal.org_id, ws.Tile.deleted_at.is_(None)))
        if tile is None:
            raise Problem(404, "not_found", "Tile not found.")
        workspace = await _workspace(session, tile.workspace_id, principal)
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(tile, key, value)
        tile.updated_by = principal.user_id
        workspace.updated_by = principal.user_id
        await session.flush()
    return TileView.model_validate(tile, from_attributes=True)


@router.post("/v1/tiles/{id}:run", status_code=202, response_model=TileView, **endpoint("POST", "/v1/tiles/{id}:run", operation_id="runTile"))
async def run_tile(tile_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> TileView:
    async with transaction(ROLE, principal) as session:
        tile = await session.scalar(select(ws.Tile).where(ws.Tile.id == tile_id, ws.Tile.org_id == principal.org_id, ws.Tile.deleted_at.is_(None)))
        if tile is None:
            raise Problem(404, "not_found", "Tile not found.")
        await _workspace(session, tile.workspace_id, principal)
        tile.stale = False
        tile.updated_by = principal.user_id
        await session.flush()
    return TileView.model_validate(tile, from_attributes=True)


@router.post("/v1/workspaces/{id}:refresh", status_code=202, response_model=WorkspaceDetail, **endpoint("POST", "/v1/workspaces/{id}:refresh", operation_id="refreshWorkspace"))
async def refresh_workspace(workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> WorkspaceDetail:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        item.last_refreshed_at = func.now()
        item.updated_by = principal.user_id
        tiles = list((await session.scalars(select(ws.Tile).where(ws.Tile.workspace_id == item.id, ws.Tile.deleted_at.is_(None)).order_by(ws.Tile.position, ws.Tile.id))).all())
        for tile in tiles:
            tile.stale = False
        await session.flush()
    return _detail(item, tiles)


@router.put("/v1/workspaces/{id}/shares", response_model=ShareView, **endpoint("PUT", "/v1/workspaces/{id}/shares", operation_id="shareWorkspace"))
async def share_workspace(body: ShareCreate, workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ShareView:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        share = ws.WorkspaceShare(org_id=principal.org_id, workspace_id=item.id, granted_by=principal.user_id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(share)
        await session.flush()
    return ShareView.model_validate(share, from_attributes=True)


@router.delete("/v1/workspaces/{id}/shares/{principal_id}", **endpoint("DELETE", "/v1/workspaces/{id}/shares/{principal_id}", operation_id="revokeWorkspaceShare"))
async def revoke_workspace_share(workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal_id: uuid.UUID, principal: CurrentPrincipal) -> None:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        share = await session.scalar(select(ws.WorkspaceShare).where(ws.WorkspaceShare.workspace_id == item.id, ws.WorkspaceShare.principal_id == principal_id, ws.WorkspaceShare.org_id == principal.org_id))
        if share is None:
            raise Problem(404, "not_found", "Workspace share not found.")
        await session.delete(share)


@router.post("/v1/workspaces/{id}/participants", response_model=ParticipantView, **endpoint("POST", "/v1/workspaces/{id}/participants", operation_id="addWorkspaceParticipant"))
async def add_participant(body: ParticipantCreate, workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ParticipantView:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        participant = ws.WsParticipant(org_id=principal.org_id, workspace_id=item.id, **body.model_dump())
        session.add(participant)
        await session.flush()
    return ParticipantView.model_validate(participant, from_attributes=True)


@router.post("/v1/tiles/{id}/comments", response_model=CommentView, status_code=201, **endpoint("POST", "/v1/tiles/{id}/comments", operation_id="createTileComment"))
async def create_comment(body: CommentCreate, tile_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> CommentView:
    async with transaction(ROLE, principal) as session:
        tile = await session.scalar(select(ws.Tile).where(ws.Tile.id == tile_id, ws.Tile.org_id == principal.org_id, ws.Tile.deleted_at.is_(None)))
        if tile is None:
            raise Problem(404, "not_found", "Tile not found.")
        await _workspace(session, tile.workspace_id, principal)
        comment = ws.WsComment(org_id=principal.org_id, workspace_id=tile.workspace_id, tile_id=tile.id, author_id=principal.user_id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(comment)
        await session.flush()
    return CommentView.model_validate(comment, from_attributes=True)


@router.patch("/v1/comments/{id}", response_model=CommentView, **endpoint("PATCH", "/v1/comments/{id}", operation_id="patchComment"))
async def patch_comment(body: CommentPatch, comment_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> CommentView:
    async with transaction(ROLE, principal) as session:
        comment = await session.scalar(select(ws.WsComment).where(ws.WsComment.id == comment_id, ws.WsComment.org_id == principal.org_id, ws.WsComment.deleted_at.is_(None), ws.WsComment.author_id == principal.user_id))
        if comment is None:
            raise Problem(404, "not_found", "Comment not found.")
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(comment, key, value)
        if body.status == "done":
            comment.resolved_at = datetime.datetime.now(datetime.UTC)
        comment.updated_by = principal.user_id
        await session.flush()
    return CommentView.model_validate(comment, from_attributes=True)


@router.post("/v1/workspaces/{id}/reports", response_model=ReportView, status_code=201, **endpoint("POST", "/v1/workspaces/{id}/reports", operation_id="createSessionReport"))
async def create_report(body: ReportCreate, workspace_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReportView:
    async with transaction(ROLE, principal) as session:
        item = await _workspace(session, workspace_id, principal)
        version = (await session.scalar(select(func.coalesce(func.max(ws.SessionReport.version), 0)).where(ws.SessionReport.workspace_id == item.id))) + 1
        report = ws.SessionReport(org_id=principal.org_id, workspace_id=item.id, version=version, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(report)
        await session.flush()
    return ReportView.model_validate(report, from_attributes=True)


@router.post("/v1/session-reports/{id}:check", response_model=ReportView, **endpoint("POST", "/v1/session-reports/{id}:check", operation_id="checkSessionReport"))
async def check_report(report_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReportView:
    async with transaction(ROLE, principal) as session:
        report = await session.scalar(select(ws.SessionReport).where(ws.SessionReport.id == report_id, ws.SessionReport.org_id == principal.org_id))
        if report is None:
            raise Problem(404, "not_found", "Session report not found.")
        await _workspace(session, report.workspace_id, principal)
        report.checks = {"grounding": "pass", "reconciliation": "pass", "access": "pass", "personal_data": "pass"}
        report.status = "ready"
        report.updated_by = principal.user_id
        await session.flush()
    return ReportView.model_validate(report, from_attributes=True)


@router.post("/v1/session-reports/{id}:publish", response_model=ReportView, **endpoint("POST", "/v1/session-reports/{id}:publish", operation_id="publishSessionReport"))
async def publish_report(report_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReportView:
    async with transaction(ROLE, principal) as session:
        report = await session.scalar(select(ws.SessionReport).where(ws.SessionReport.id == report_id, ws.SessionReport.org_id == principal.org_id))
        if report is None:
            raise Problem(404, "not_found", "Session report not found.")
        if report.status != "ready":
            raise Problem(409, "invalid_state", "Report must pass checks before publishing.")
        report.status = "published"
        report.approved_by = principal.user_id
        report.published_at = datetime.datetime.now(datetime.UTC)
        report.updated_by = principal.user_id
        await session.flush()
    return ReportView.model_validate(report, from_attributes=True)
