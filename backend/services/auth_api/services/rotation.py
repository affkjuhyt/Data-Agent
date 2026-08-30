import asyncio
from datetime import datetime, timedelta, timezone

import psycopg2.extras

try:
    from services.auth_api.domain.secrets import TRANSIT_KEY_TYPES, TRANSIT_MOUNT, build_transit_key_name
    from services.auth_api.infrastructure.config import log
    from services.auth_api.infrastructure.db import get_db
    from services.auth_api.infrastructure.vault import get_vault
    from services.auth_api.services.governance import get_governance_setting
except ModuleNotFoundError:
    from auth_api.domain.secrets import TRANSIT_KEY_TYPES, TRANSIT_MOUNT, build_transit_key_name
    from auth_api.infrastructure.config import log
    from auth_api.infrastructure.db import get_db
    from auth_api.infrastructure.vault import get_vault
    from auth_api.services.governance import get_governance_setting


def run_auto_rotation() -> None:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not get_governance_setting(cur, "vault_auto_rotation"):
                return
            cur.execute(
                "SELECT id, tenant_id, key_name, key_type, version "
                "FROM secrets_vault WHERE is_active = true AND rotation_due_at < now()"
            )
            overdue = cur.fetchall()
    finally:
        conn.close()

    if not overdue:
        return

    vault = get_vault()
    for row in overdue:
        conn = get_db()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if row["key_type"] in TRANSIT_KEY_TYPES:
                    transit_key_name = build_transit_key_name(row["tenant_id"], row["key_name"])
                    try:
                        vault.secrets.transit.rotate_key(name=transit_key_name, mount_point=TRANSIT_MOUNT)
                        status, error = "SUCCESS", None
                    except Exception as exc:
                        status, error = "FAILED", str(exc)
                    new_version = row["version"] + 1
                    rotation_due = datetime.now(timezone.utc) + timedelta(days=90)
                    cur.execute(
                        "UPDATE secrets_vault SET version=%s, rotation_due_at=%s, last_rotated_at=now() WHERE id=%s",
                        (new_version, rotation_due, row["id"]),
                    )
                    cur.execute(
                        "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status, error) "
                        "VALUES (%s,'SCHEDULED','system',%s,%s,%s,%s)",
                        (row["id"], row["version"], new_version, status, error),
                    )
                    log.info("auto_rotation", key=row["key_name"], status=status)
                else:
                    cur.execute(
                        "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status, error) "
                        "VALUES (%s,'SCHEDULED','system',%s,%s,'FAILED',%s)",
                        (
                            row["id"],
                            row["version"],
                            row["version"],
                            "Manual rotation required — KV secrets need a new value",
                        ),
                    )
                    log.warning("auto_rotation_skipped_kv", key=row["key_name"])
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.error("auto_rotation_error", key=row["key_name"], error=str(exc))
        finally:
            conn.close()


async def auto_rotation_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(run_auto_rotation)
        except Exception as exc:
            log.error("auto_rotation_loop_error", error=str(exc))
        await asyncio.sleep(86400)
