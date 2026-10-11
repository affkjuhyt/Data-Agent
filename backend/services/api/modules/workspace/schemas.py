"""Wire shapes for the workspace module."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    params: dict[str, Any] = Field(default_factory=dict)
    created_from: Literal["blank", "conversation", "template", "agent_run"] = "blank"
    created_from_ref: uuid.UUID | None = None


class WorkspacePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    params: dict[str, Any] | None = None


class TileCreate(BaseModel):
    position: int = Field(ge=0)
    kind: Literal["question", "sql", "chart", "text", "skill_run", "agent_result", "table"]
    title: str | None = Field(default=None, max_length=500)
    spec: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[uuid.UUID] = Field(default_factory=list)


class TilePatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    spec: dict[str, Any] | None = None
    position: int | None = Field(default=None, ge=0)
    agent_suggestion_state: Literal["suggested", "accepted", "dismissed"] | None = None


class TileView(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    position: int
    kind: str
    title: str | None
    spec: Any
    depends_on: list[uuid.UUID]
    author_kind: str
    agent_suggestion_state: str | None
    stale: bool


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None
    params: Any
    tile_count: int
    status: str
    updated_at: datetime.datetime


class WorkspaceDetail(WorkspaceSummary):
    tiles: list[TileView]


class ShareCreate(BaseModel):
    principal_type: Literal["user", "group"]
    principal_id: uuid.UUID
    permission: Literal["view", "comment", "edit"]
    expires_at: datetime.datetime | None = None


class ShareView(ShareCreate):
    id: uuid.UUID
    workspace_id: uuid.UUID


class ParticipantCreate(BaseModel):
    principal_type: Literal["user", "agent"]
    principal_id: uuid.UUID
    role: Literal["owner", "editor", "viewer", "agent_helper"]


class ParticipantView(ParticipantCreate):
    id: uuid.UUID
    workspace_id: uuid.UUID
    joined_at: datetime.datetime


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8_000)
    mentions: list[uuid.UUID] = Field(default_factory=list)
    assignee_id: uuid.UUID | None = None
    due_at: datetime.datetime | None = None


class CommentPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=8_000)
    status: Literal["open", "done"] | None = None


class CommentView(CommentCreate):
    id: uuid.UUID
    workspace_id: uuid.UUID
    tile_id: uuid.UUID | None
    author_id: uuid.UUID
    status: str
    resolved_at: datetime.datetime | None


class ReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body_ref: str = Field(min_length=1, max_length=2_000)


class ReportView(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    version: int
    title: str
    body_ref: str
    checks: Any
    status: str
    approved_by: uuid.UUID | None
    published_at: datetime.datetime | None
