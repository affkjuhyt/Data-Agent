from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import AuthPlatformSettingORM
try:
    from services.auth_api.infrastructure.db import get_session
except ModuleNotFoundError:
    from auth_api.infrastructure.db import get_session

GOVERNANCE_KEYS = [
    "vault_auto_rotation",
    "vault_panic_mode",
    "vault_pii_access_log",
]


def get_governance_setting(db: Session, key: str) -> bool:
    value = db.scalar(select(AuthPlatformSettingORM.value).where(AuthPlatformSettingORM.key == key))
    return str(value).lower() == "true" if value is not None else False


def enforce_vault_not_in_panic_mode() -> None:
    with get_session() as session:
        if get_governance_setting(session, "vault_panic_mode"):
            raise HTTPException(
                status_code=503,
                detail="Vault is in PANIC MODE — all secret access suspended. Disable via governance settings.",
            )
