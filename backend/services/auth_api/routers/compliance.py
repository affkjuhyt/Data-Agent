"""Compliance control-plane: per-tenant PII / residency / retention settings,
a computed compliance score, and a PII scan/preview endpoint.

Backs the frontend "Security & Compliance Center". Settings are persisted in
``compliance_settings`` (created idempotently in ``auth_core._ensure_settings_table``)
and read at ingestion time by the KB worker to gate PII redaction — so toggling
``pii_masking`` here is a real enforcement switch, not a cosmetic flag.
"""

from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import ComplianceSettingORM
from libs.shared.auth import require_role
from libs.shared.pii_detector import PII_TYPES, scan as pii_scan
try:
    from services.auth_api.infrastructure.config import log
    from services.auth_api.infrastructure.db import get_session, orm_to_dict
    from services.auth_api.schemas import ComplianceSettingsInput, PiiScanInput
except ModuleNotFoundError:
    from auth_api.infrastructure.config import log
    from auth_api.infrastructure.db import get_session, orm_to_dict
    from auth_api.schemas import ComplianceSettingsInput, PiiScanInput

router = APIRouter()

_DEFAULTS: dict[str, Any] = {
    "pii_masking":         False,
    "pii_types":           list(PII_TYPES),
    "residency_region":    "Asia-SE1",
    "residency_standards": "GDPR",
    "retention_days":      365,
    "retention_lock":      False,
    "zero_trust":          False,
    "audit_residency":     False,
}


def _row_to_settings(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {**_DEFAULTS, "updated_at": None, "updated_by": ""}
    raw_types = (row.get("pii_types") or "").strip()
    types = [t for t in (p.strip().upper() for p in raw_types.split(",")) if t] or list(PII_TYPES)
    return {
        "pii_masking":         bool(row["pii_masking"]),
        "pii_types":           types,
        "residency_region":    row["residency_region"] or "",
        "residency_standards": row["residency_standards"] or "",
        "retention_days":      int(row["retention_days"]),
        "retention_lock":      bool(row["retention_lock"]),
        "zero_trust":          bool(row["zero_trust"]),
        "audit_residency":     bool(row["audit_residency"]),
        "updated_at":          row["updated_at"].isoformat() if row.get("updated_at") else None,
        "updated_by":          row.get("updated_by") or "",
    }


def _fetch(session: Any, tenant_id: str) -> dict[str, Any]:
    row = session.scalar(
        select(ComplianceSettingORM).where(ComplianceSettingORM.tenant_id == tenant_id)
    )
    return _row_to_settings(orm_to_dict(row) if row is not None else None)


def _compute_score(s: dict[str, Any]) -> dict[str, Any]:
    """Derive a 0–100 compliance score from the tenant's *actual* persisted
    controls (no hardcoded numbers). Three weighted pillars."""
    data_protection = (60 if s["pii_masking"] else 0) + (
        40 if s["retention_lock"] else (20 if s["retention_days"] > 0 else 0)
    )
    data_residency = (60 if s["residency_region"] else 0) + (40 if s["residency_standards"] else 0)
    access_governance = (50 if s["zero_trust"] else 0) + (50 if s["audit_residency"] else 0)

    overall = round(
        0.4 * data_protection + 0.3 * data_residency + 0.3 * access_governance, 1
    )
    return {
        "score": overall,
        "breakdown": [
            {"label": "Data Protection", "val": data_protection},
            {"label": "Data Residency",  "val": data_residency},
            {"label": "Access Governance", "val": access_governance},
        ],
        "policies": [
            {"id": "pol-pii", "name": "PII Redaction (Ingestion)", "module": "Knowledge Base",
             "status": "COMPLIANT" if s["pii_masking"] else "PENDING_SCAN",
             "severity": "HIGH"},
            {"id": "pol-zt", "name": "Zero-Trust Model Access", "module": "AI Runtime",
             "status": "COMPLIANT" if s["zero_trust"] else "DISABLED",
             "severity": "CRITICAL"},
            {"id": "pol-ret", "name": "Retention Lock (Gold Layer)", "module": "Storage",
             "status": "COMPLIANT" if s["retention_lock"] else "UNLOCKED",
             "severity": "MEDIUM"},
            {"id": "pol-aud", "name": "Audit Trace Residency", "module": "Governance",
             "status": "COMPLIANT" if s["audit_residency"] else "PENDING_SCAN",
             "severity": "HIGH"},
        ],
    }


@router.get("/api/auth/compliance/settings")
def get_settings(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session() as session:
        return _fetch(session, x_tenant_id)


@router.put("/api/auth/compliance/settings")
def put_settings(
    body: ComplianceSettingsInput,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session() as session:
        current = _fetch(session, x_tenant_id)
        merged = {**current}
        data = body.model_dump(exclude_none=True)
        for key in _DEFAULTS:
            if key in data:
                merged[key] = data[key]

        # Validate + normalize PII types against the known set.
        valid = [t for t in PII_TYPES]
        types = [t.strip().upper() for t in merged["pii_types"] if t and t.strip()]
        merged["pii_types"] = [t for t in types if t in valid] or list(PII_TYPES)
        if int(merged["retention_days"]) < 0:
            raise HTTPException(status_code=422, detail="retention_days must be >= 0")

        values = {
            "tenant_id": x_tenant_id,
            "pii_masking": merged["pii_masking"],
            "pii_types": ",".join(merged["pii_types"]),
            "residency_region": merged["residency_region"],
            "residency_standards": merged["residency_standards"],
            "retention_days": int(merged["retention_days"]),
            "retention_lock": merged["retention_lock"],
            "zero_trust": merged["zero_trust"],
            "audit_residency": merged["audit_residency"],
            "updated_by": user["user_id"],
        }
        stmt = (
            pg_insert(ComplianceSettingORM)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[ComplianceSettingORM.tenant_id],
                set_={**values, "updated_at": func.now()},
            )
            .returning(ComplianceSettingORM)
        )
        # populate_existing: _fetch above already put this row in the identity map, and
        # a Core upsert does not refresh it — without this the response is the old row.
        row = session.scalar(stmt, execution_options={"populate_existing": True})
        result = _row_to_settings(orm_to_dict(row))
        log.info("compliance_settings_updated", tenant=x_tenant_id, actor=user["user_id"],
                 pii_masking=result["pii_masking"])
        return result


@router.get("/api/auth/compliance/score")
def get_score(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session() as session:
        return _compute_score(_fetch(session, x_tenant_id))


@router.post("/api/auth/compliance/pii-scan")
def post_pii_scan(
    body: PiiScanInput,
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    result: dict[str, Any] = pii_scan(body.text, body.types)
    return result
