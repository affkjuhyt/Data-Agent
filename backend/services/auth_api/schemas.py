from typing import Optional

from pydantic import BaseModel


class IPRuleIn(BaseModel):
    cidr: str
    label: str = ""
    is_active: bool = True


class APIKeyIn(BaseModel):
    name: str
    scope: str = "read_only"
    expires_at: Optional[str] = None


class SecretIn(BaseModel):
    key_name: str
    key_type: str
    algorithm: str = ""
    realm: str = ""
    value: str = ""
    rotation_days: int = 90


class ComplianceSettingsIn(BaseModel):
    pii_masking:         Optional[bool] = None
    pii_types:           Optional[list[str]] = None
    residency_region:    Optional[str] = None
    residency_standards: Optional[str] = None
    retention_days:      Optional[int] = None
    retention_lock:      Optional[bool] = None
    zero_trust:          Optional[bool] = None
    audit_residency:     Optional[bool] = None


class PiiScanIn(BaseModel):
    text:  str
    types: Optional[list[str]] = None


class SignIn(BaseModel):
    data: str


class VerifyIn(BaseModel):
    data: str
    signature: str
