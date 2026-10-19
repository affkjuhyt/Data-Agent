"""Wire shapes of the workspace module: request bodies are tab 4 A3's, responses the D4 columns
of the table minus org_id, created_by, updated_by and deleted_at."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The levels a caller holds on a workspace, weakest first (owner > share/participant grants).
Permission = Literal["view", "comment", "edit", "owner"]


class _Body(BaseModel):
    # A field A3 does not name is a client bug, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(_Body):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    params: dict[str, Any] = Field(default_factory=dict)
    created_from: Literal["blank", "conversation", "template", "agent_run"] = "blank"
    created_from_ref: uuid.UUID | None = None
    template_of_skill_id: uuid.UUID | None = None


class WorkspacePatch(_Body):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    params: dict[str, Any] | None = None
    status: Literal["active", "archived"] | None = None


class TileCreate(_Body):
    kind: Literal["question", "sql", "chart", "text", "skill_run", "agent_result", "table"]
    title: str | None = Field(default=None, max_length=500)
    spec: dict[str, Any]
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    # Omitted: the tile goes after the last one.
    position: int | None = Field(default=None, ge=0)


class TilePatch(_Body):
    title: str | None = Field(default=None, max_length=500)
    spec: dict[str, Any] | None = None
    depends_on: list[uuid.UUID] | None = None
    position: int | None = Field(default=None, ge=0)
    # "suggested" is the agent's to set; a person accepts or dismisses (UC-02-06 step 5).
    agent_suggestion_state: Literal["accepted", "dismissed"] | None = None


class RefreshRequest(_Body):
    as_of: datetime.date | None = None


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 1_000


class TileView(_View):
    id: uuid.UUID
    workspace_id: uuid.UUID
    position: int
    kind: str
    title: str | None
    spec: Any
    depends_on: list[uuid.UUID]
    author_kind: str
    agent_suggestion_state: str | None
    latest_snapshot_id: uuid.UUID | None
    stale: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkspaceView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None
    params: Any
    template_of_skill_id: uuid.UUID | None
    created_from: str
    created_from_ref: uuid.UUID | None
    tile_count: int
    last_refreshed_at: datetime.datetime | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ParticipantView(_View):
    id: uuid.UUID
    workspace_id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    role: str
    joined_at: datetime.datetime


class WorkspaceDetail(WorkspaceView):
    tiles: list[TileView]
    participants: list[ParticipantView]
    permission: Permission


class ShareGrant(_Body):
    principal_type: Literal["user", "group"]
    principal_id: uuid.UUID
    permission: Literal["view", "comment", "edit"]
    expires_at: datetime.datetime | None = None


class SharesPut(_Body):
    """PUT replaces the whole set (A1.3): a principal left out loses its share."""

    shares: list[ShareGrant] = Field(max_length=500)

    @field_validator("shares")
    @classmethod
    def _one_grant_per_principal(cls, shares: list[ShareGrant]) -> list[ShareGrant]:
        keys = [(s.principal_type, s.principal_id) for s in shares]
        if len(keys) != len(set(keys)):
            raise ValueError("a principal appears more than once")
        return shares


class ShareView(_View):
    id: uuid.UUID
    workspace_id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    permission: str
    granted_by: uuid.UUID
    expires_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # The user's or group's name, so the share dialog never shows an id; null once deleted.
    principal_name: str | None = None


# Why a recipient would not see a tile (POST /v1/workspaces/{id}/shares:preview, BR-89):
#   not_in_organisation    the principal is no active user or group of the organisation (E1)
#   not_data_space_member  not a current member of the workspace's Data Space (2a: UC-11-07)
#   missing_capability     a member whose roles do not include `ask`, so it cannot open sessions
#   other_data_space       the tile's last result read a Data Space the recipient is not in
HiddenReason = Literal[
    "not_in_organisation", "not_data_space_member", "missing_capability", "other_data_space"
]


class HiddenTile(BaseModel):
    tile_id: uuid.UUID
    reason: HiddenReason


class RecipientPreview(BaseModel):
    """What one recipient would see if the shares were saved; nothing is written."""

    principal_type: str
    principal_id: uuid.UUID
    permission: str
    # False when the recipient could not open the session at all (every tile hidden).
    can_open: bool
    reason: HiddenReason | None
    # A row scope on the recipient's membership: the tiles run, with fewer rows (BR-07).
    row_scope_limited: bool
    visible_tile_ids: list[uuid.UUID]
    hidden_tiles: list[HiddenTile]


class ParticipantCreate(_Body):
    principal_type: Literal["user", "agent"]
    principal_id: uuid.UUID
    role: Literal["owner", "editor", "viewer", "agent_helper"]


class CommentCreate(_Body):
    body: str = Field(min_length=1, max_length=8_000)
    parent_id: uuid.UUID | None = None
    mentions: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    assignee_id: uuid.UUID | None = None
    due_at: datetime.datetime | None = None


class CommentPatch(_Body):
    body: str | None = Field(default=None, min_length=1, max_length=8_000)
    status: Literal["open", "done"] | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime.datetime | None = None


class CommentView(_View):
    id: uuid.UUID
    workspace_id: uuid.UUID
    tile_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    author_id: uuid.UUID
    body: str
    mentions: list[uuid.UUID]
    assignee_id: uuid.UUID | None
    due_at: datetime.datetime | None
    status: str
    resolved_at: datetime.datetime | None
    agent_run_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Names of the author and the assignee for the thread; null for one no longer in org.
    author_name: str | None = None
    assignee_name: str | None = None


class ReportCreate(_Body):
    title: str = Field(min_length=1, max_length=500)
    # Omitted: every tile of the workspace.
    tile_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class Recipient(_Body):
    principal_type: Literal["user", "group"]
    principal_id: uuid.UUID
    channels: list[str] = Field(default_factory=lambda: ["email"], min_length=1)


class PublishRequest(_Body):
    recipients: list[Recipient] = Field(default_factory=list, max_length=500)


class ReportView(_View):
    id: uuid.UUID
    workspace_id: uuid.UUID
    version: int
    title: str
    body_ref: str
    checks: Any
    status: str
    approved_by: uuid.UUID | None
    published_at: datetime.datetime | None
    hitl_request_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ReportDocumentView(BaseModel):
    """The report worker's immutable markdown artifact, read through the API."""

    report_id: uuid.UUID
    body_ref: str
    content_type: Literal["text/markdown"]
    markdown: str
