from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import (
    AuthPlatformSettingORM,
    ComplianceSettingORM,
)
try:
    from services.auth_api.infrastructure.db import get_session
except ModuleNotFoundError:
    from auth_api.infrastructure.db import get_session

# Platform-wide switches, in platform_settings. Panic mode is not one of them:
# it is per tenant (compliance_settings.vault_panic_mode), see get_panic_mode.
GOVERNANCE_KEYS = [
    "vault_auto_rotation",
    "vault_pii_access_log",
]
PANIC_MODE_KEY = "vault_panic_mode"


def get_governance_setting(db: Session, key: str) -> bool:
    value = db.scalar(select(AuthPlatformSettingORM.value).where(AuthPlatformSettingORM.key == key))
    return str(value).lower() == "true" if value is not None else False


def get_panic_mode(db: Session, tenant_id: str) -> bool:
    """Whether this tenant's vault is in panic mode.

    It used to be one platform_settings flag: a panic triggered for one tenant
    revoked that tenant's secrets but blocked every tenant's secret routes.
    """
    return bool(
        db.scalar(
            select(ComplianceSettingORM.vault_panic_mode).where(
                ComplianceSettingORM.tenant_id == tenant_id
            )
        )
    )


def set_panic_mode(db: Session, tenant_id: str, enabled: bool) -> None:
    db.execute(
        pg_insert(ComplianceSettingORM)
        .values(tenant_id=tenant_id, vault_panic_mode=enabled)
        .on_conflict_do_update(
            index_elements=[ComplianceSettingORM.tenant_id],
            set_={"vault_panic_mode": enabled},
        )
    )


def enforce_vault_not_in_panic_mode(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> None:
    with get_session(x_tenant_id) as session:
        if get_panic_mode(session, x_tenant_id):
            raise HTTPException(
                status_code=503,
                detail="Vault is in PANIC MODE for this tenant — secret access suspended. Disable via governance settings.",
            )
