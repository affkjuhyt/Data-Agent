"""Wire shapes of the org module (tab 4 A3 · Quản trị)."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class RoleRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class DataSpaceSummary(BaseModel):
    """GET /v1/data-spaces: a Data Space the caller is a member of, with the
    caller's roles there (A3: "kèm vai trò của người gọi")."""

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    status: str
    steward_id: uuid.UUID
    opened_at: datetime.datetime | None
    my_roles: list[RoleRef]
    # Capabilities those roles grant in this Data Space, on top of the
    # organisation-wide ones.
    my_capabilities: list[str]


class RoleGrant(BaseModel):
    """One way the caller holds an organisation-wide role: assigned directly
    (manual, emergency) or through a directory group (group_mapping)."""

    role: RoleRef
    source: str
    # The group the role comes through, when source is group_mapping.
    group_name: str | None = None
    # Set for emergency access (BR-44, at most 24 h).
    valid_until: datetime.datetime | None = None


class Me(BaseModel):
    """GET /v1/me. Identity comes from the directory; roles per Data Space are
    in GET /v1/data-spaces."""

    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    department: str | None
    kind: str
    default_data_space_id: uuid.UUID | None
    locale: str
    time_zone: str
    roles: list[RoleGrant]
    capabilities: list[str]


class DataSpacePreferencePut(BaseModel):
    open_last_used: bool
    data_space_id: uuid.UUID | None = Field(default=None)


class DataSpacePreferenceView(BaseModel):
    open_last_used: bool
    data_space_id: uuid.UUID | None


class OnboardingStep(BaseModel):
    key: str
    label: str
    state: str


class AgentTemplateChoice(BaseModel):
    ref: str
    title: str
    description: str
    badge: dict[str, bool | str]


class OnboardingView(BaseModel):
    state: str
    steps: list[OnboardingStep]
    context_text: str
    templates: list[AgentTemplateChoice]


class OnboardingAgentRequest(BaseModel):
    template_ref: str


class OnboardingAgentAccepted(BaseModel):
    agent_id: uuid.UUID
    run_id: uuid.UUID
