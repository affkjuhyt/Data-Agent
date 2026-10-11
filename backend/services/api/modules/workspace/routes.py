"""Core workspace and tile operations for API-03."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select

from libs.dataagent_db.models import workspace as ws
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, invalid_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.modules.workspace.schemas import (
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
