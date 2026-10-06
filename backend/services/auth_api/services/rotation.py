import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import KeyRotationORM, SecretVaultORM

from services.auth_api.domain.secrets import (
    TRANSIT_KEY_TYPES,
    TRANSIT_MOUNT,
    build_transit_key_name,
)
from services.auth_api.infrastructure.config import log
from services.auth_api.infrastructure.db import get_session
from services.auth_api.infrastructure.vault import get_vault
from services.auth_api.services.governance import get_governance_setting


def run_auto_rotation() -> None:
    with get_session() as session:
        if not get_governance_setting(session, "vault_auto_rotation"):
            return
        # Every tenant's overdue secrets: auth_secrets_due_for_rotation is the
        # SECURITY DEFINER read across tenants. Each is then rotated under its own.
        overdue = session.execute(text("SELECT * FROM auth_secrets_due_for_rotation()")).all()

    if not overdue:
        return

    vault = get_vault()
    for secret_id, tenant_id in overdue:
        row: SecretVaultORM | None = None
        try:
            with get_session(str(tenant_id)) as session:
                row = session.scalar(
                    select(SecretVaultORM)
                    .where(
                        SecretVaultORM.id == secret_id,
                        SecretVaultORM.is_active.is_(True),
                        SecretVaultORM.rotation_due_at < datetime.now(timezone.utc),
                    )
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    continue
                old_version = row.version
                if row.key_type in TRANSIT_KEY_TYPES:
                    transit_key_name = build_transit_key_name(str(row.tenant_id), row.key_name)
                    try:
                        vault.secrets.transit.rotate_key(name=transit_key_name, mount_point=TRANSIT_MOUNT)
                        status, error = "SUCCESS", None
                    except Exception as exc:
                        status, error = "FAILED", str(exc)
                    new_version = old_version + 1 if status == "SUCCESS" else old_version
                    if status == "SUCCESS":
                        row.version = new_version
                        row.rotation_due_at = datetime.now(timezone.utc) + timedelta(days=90)
                        row.last_rotated_at = datetime.now(timezone.utc)
                    session.add(
                        KeyRotationORM(
                            secret_id=row.id,
                            triggered_by="SCHEDULED",
                            actor_id="system",
                            old_version=old_version,
                            new_version=new_version,
                            status=status,
                            error=error,
                        )
                    )
                    log.info("auto_rotation", key=row.key_name, status=status)
                else:
                    session.add(
                        KeyRotationORM(
                            secret_id=row.id,
                            triggered_by="SCHEDULED",
                            actor_id="system",
                            old_version=row.version,
                            new_version=row.version,
                            status="FAILED",
                            error="Manual rotation required — KV secrets need a new value",
                        )
                    )
                    log.warning("auto_rotation_skipped_kv", key=row.key_name)
        except Exception as exc:
            log.error("auto_rotation_error", key=getattr(row, "key_name", str(secret_id)), error=str(exc))


async def auto_rotation_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(run_auto_rotation)
        except Exception as exc:
            log.error("auto_rotation_loop_error", error=str(exc))
        await asyncio.sleep(86400)
