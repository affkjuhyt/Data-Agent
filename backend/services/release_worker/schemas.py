from typing import Optional

from pydantic import BaseModel


class TriggerPipelineRequest(BaseModel):
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    package_version: str
    pipeline_name: Optional[str] = None
    target_environments: list[str] = ["dev"]


class ApprovalRequest(BaseModel):
    decision: str
    comment: Optional[str] = None


class RollbackRequest(BaseModel):
    from_version: str
    to_version: str
    environment: str
    reason: Optional[str] = None


class DriftResolveRequest(BaseModel):
    notes: Optional[str] = None
