from datetime import datetime
from typing import Any, Optional

VALID_KEY_TYPES = {
    "ENCRYPTION_KEY",
    "SIGNING_KEY",
    "HMAC_KEY",
    "BEARER_TOKEN",
    "MCP_TOKEN",
    "KB_API_KEY",
    "WAREHOUSE_PASSWORD",
    "DB_CONNECTOR_PASSWORD",
}

TENANT_MANAGEABLE_KEY_TYPES = {"DB_CONNECTOR_PASSWORD"}
VAULT_MOUNT = "secret"
TRANSIT_MOUNT = "transit"
TRANSIT_KEY_TYPES = {"SIGNING_KEY", "ENCRYPTION_KEY", "HMAC_KEY"}
TRANSIT_ALGORITHM_MAP: dict[str, str] = {
    "RSA-4096": "rsa-4096",
    "RSA-2048": "rsa-2048",
    "EC-P256": "ecdsa-p256",
    "HMAC-SHA256": "aes256-gcm96",
    "AES-256": "aes256-gcm96",
    "ChaCha20": "chacha20-poly1305",
}


def build_vault_path(tenant_id: str, key_name: str) -> str:
    return f"tenants/{tenant_id}/{key_name}"


def build_transit_key_name(tenant_id: str, key_name: str) -> str:
    safe = key_name.lower().replace("_", "-").replace(" ", "-")
    return f"{tenant_id[:8]}-{safe}"[:63]


def format_secret(row: dict[str, Any]) -> dict[str, Any]:
    def isoformat_or_none(value: Any) -> Optional[str]:
        return value.isoformat() if isinstance(value, datetime) else None if value is None else str(value)

    return {
        "id": str(row["id"]),
        "key_name": row["key_name"],
        "key_type": row["key_type"],
        "algorithm": row["algorithm"] or "",
        "realm": row["realm"] or "",
        "version": row["version"],
        "is_active": row["is_active"],
        "rotation_due_at": isoformat_or_none(row["rotation_due_at"]),
        "last_rotated_at": isoformat_or_none(row["last_rotated_at"]),
        "created_by": row["created_by"] or "",
        "created_at": isoformat_or_none(row["created_at"]),
    }
