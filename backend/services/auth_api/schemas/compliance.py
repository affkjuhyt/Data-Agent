from typing import Optional

from pydantic import BaseModel


class ComplianceSettingsInput(BaseModel):
    pii_masking: Optional[bool] = None
    pii_types: Optional[list[str]] = None
    residency_region: Optional[str] = None
    residency_standards: Optional[str] = None
    retention_days: Optional[int] = None
    retention_lock: Optional[bool] = None
    zero_trust: Optional[bool] = None
    audit_residency: Optional[bool] = None


class PiiScanInput(BaseModel):
    text: str
    types: Optional[list[str]] = None
