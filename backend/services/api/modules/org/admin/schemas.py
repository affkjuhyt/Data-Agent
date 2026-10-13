"""Wire shapes of the Quản trị endpoints over org.* (tab 4 A3): body fields as A3 names them, types
from the tab 3b D4.1 column of the same name; responses are the rows minus org_id, created_by,
updated_by and deleted_at."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services.api.modules.org.schemas import RoleRef

PrincipalType = Literal["user", "group", "agent"]
DataSpaceStatus = Literal["draft", "onboarding", "active", "read_only", "archived"]
AccessCapability = Literal["view_aggregate", "view_detail", "ask", "run_agent"]


class _Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── users ──


class SpaceRoles(BaseModel):
    data_space_id: uuid.UUID
    data_space_code: str
    roles: list[RoleRef]


class AdminUser(_Row):
    """GET /v1/admin/users: the account and its effective roles, org-wide and per Data Space."""

    id: uuid.UUID
    kind: str
    username: str
    display_name: str
    email: str | None
    department: str | None
    status: str
    owner_user_id: uuid.UUID | None
    default_data_space_id: uuid.UUID | None
    last_synced_at: datetime.datetime | None
    last_login_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    org_roles: list[RoleRef] = Field(default_factory=list)
    data_space_roles: list[SpaceRoles] = Field(default_factory=list)


class GroupRef(BaseModel):
    id: uuid.UUID
    name: str


class GrantOrigin(BaseModel):
    """Where one role of the user comes from (UC-11-01 step 4): a directory group, emergency
    access, an approved access request, or a manual assignment."""

    role: RoleRef
    # None: organisation-wide.
    data_space_id: uuid.UUID | None
    source: str
    group: GroupRef | None = None
    source_ref: uuid.UUID | None = None
    valid_until: datetime.datetime | None = None
    approved_by: uuid.UUID | None = None
    row_scope: Any | None = None


class AdminUserDetail(AdminUser):
    attributes: dict[str, Any]
    groups: list[GroupRef]
    grants: list[GrantOrigin]


# ── group mappings ──


class GroupMappingIn(BaseModel):
    group_id: uuid.UUID
    role_id: uuid.UUID
    # None maps the group to an organisation-wide role.
    data_space_id: uuid.UUID | None = None
    row_scope: dict[str, Any] | None = None
    status: Literal["active", "disabled"] = "active"


class GroupMappingsPut(BaseModel):
    # The organisation's whole mapping set: a mapping left out is disabled.
    mappings: list[GroupMappingIn]
    # True: report who would gain or lose access (UC-11-01 step 2) without saving.
    preview: bool = False


class GroupMappingView(_Row):
    id: uuid.UUID
    group_id: uuid.UUID
    role_id: uuid.UUID
    data_space_id: uuid.UUID | None
    row_scope: Any | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class GroupMappingSet(BaseModel):
    items: list[GroupMappingView]
    next_page_token: str = ""
    # False for a preview: nothing was written.
    applied: bool
    users_added: list[uuid.UUID]
    users_removed: list[uuid.UUID]


class UserGroupView(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    source: str


class PermissionCell(BaseModel):
    capability: str
    value: Literal["yes", "approval", "no", "never"]


class PermissionRoleView(BaseModel):
    role_id: uuid.UUID
    name: str
    member_count: int
    member_unit: Literal["người", "agent"]
    cells: dict[str, Literal["yes", "approval", "no", "never"]]


class PermissionMatrix(BaseModel):
    roles: list[PermissionRoleView]
    rules: dict[str, str]


# ── emergency access ──


class EmergencyAccessIn(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2_000)
    # Defaults to 24 hours from now, the most BR-44 allows.
    valid_until: datetime.datetime | None = None
    second_approver_id: uuid.UUID


class UserRoleView(_Row):
    id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    source: str
    source_ref: uuid.UUID | None
    valid_until: datetime.datetime | None
    approved_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ── Data Spaces ──


class DefaultPolicy(BaseModel):
    """The four switches of the Tạo Data Space screen (tab 3b data_space.default_policy)."""

    model_config = ConfigDict(extra="forbid")

    mask_pii: bool | None = None
    agent_write_derived_only: bool | None = None
    show_filed_and_current: bool | None = None
    allow_external_channels: bool | None = None


class DataSpaceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    steward_id: uuid.UUID
    backup_steward_id: uuid.UUID | None = None
    default_policy: DefaultPolicy | None = None
    work_calendar_id: uuid.UUID | None = None


class DataSpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4_000)
    steward_id: uuid.UUID | None = None
    backup_steward_id: uuid.UUID | None = None
    default_policy: DefaultPolicy | None = None
    status: DataSpaceStatus | None = None
    work_calendar_id: uuid.UUID | None = None


class DataSpaceView(_Row):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    steward_id: uuid.UUID
    backup_steward_id: uuid.UUID | None
    status: str
    default_policy: dict[str, Any]
    pack_install_id: uuid.UUID | None
    work_calendar_id: uuid.UUID | None
    opened_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MemberIn(BaseModel):
    principal_type: PrincipalType
    principal_id: uuid.UUID
    role_id: uuid.UUID
    row_scope: dict[str, Any] | None = None
    valid_until: datetime.datetime | None = None


class MembersPut(BaseModel):
    # The Data Space's manual members; members from a group mapping or an access request stay.
    members: list[MemberIn]


class MemberView(_Row):
    id: uuid.UUID
    data_space_id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    role_id: uuid.UUID
    row_scope: Any | None
    source: str
    source_ref: uuid.UUID | None
    valid_until: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SourcesPut(BaseModel):
    source_ids: list[uuid.UUID]


# ── access requests ──


class AccessRequestCreate(BaseModel):
    data_space_id: uuid.UUID
    capability: AccessCapability
    reason: str = Field(min_length=1, max_length=4_000)
    requested_until: datetime.date


class GrantedScope(BaseModel):
    """What the approver actually grants when narrowing (UC-11-07 step 4)."""

    model_config = ConfigDict(extra="forbid")

    capability: AccessCapability | None = None
    row_scope: dict[str, Any] | None = None


class AccessDecision(BaseModel):
    decision: Literal["approve", "narrow", "reject"]
    granted_scope: GrantedScope | None = None
    note: str | None = Field(default=None, max_length=4_000)


class AccessRequestView(_Row):
    id: uuid.UUID
    requester_id: uuid.UUID
    data_space_id: uuid.UUID
    capability: str
    reason: str
    requested_until: datetime.date
    status: str
    needs_compliance: bool
    approver_id: uuid.UUID | None
    decided_at: datetime.datetime | None
    decision_note: str | None
    granted_scope: Any | None
    member_id: uuid.UUID | None
    hitl_request_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ── work calendars ──


class CalendarDay(_Row):
    day: datetime.date
    kind: Literal["holiday", "working_override"]
    name: str | None = Field(default=None, max_length=200)


class CalendarPut(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    time_zone: str | None = Field(default=None, min_length=1, max_length=100)
    # ISO weekdays, 1 = Monday.
    weekend_days: list[int] | None = None
    is_default: bool | None = None
    # Replaces the calendar's holidays and working overrides.
    days: list[CalendarDay] | None = None


class CalendarView(_Row):
    id: uuid.UUID
    code: str
    name: str
    time_zone: str
    weekend_days: list[int]
    is_default: bool
    days: list[CalendarDay]
    created_at: datetime.datetime
    updated_at: datetime.datetime
