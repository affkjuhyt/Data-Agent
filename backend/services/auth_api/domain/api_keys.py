import secrets
import string
from datetime import datetime, timezone
from typing import Any, Optional, cast

import bcrypt

SCOPE_PREFIX = {"full_access": "prod", "read_only": "ro", "admin_platform": "adm"}
VALID_SCOPES = set(SCOPE_PREFIX.keys())


def generate_api_key(scope: str) -> tuple[str, str]:
    suffix = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    prefix = f"sk-{SCOPE_PREFIX.get(scope, 'key')}-{''.join(secrets.choice(string.ascii_letters) for _ in range(4))}"
    return f"{prefix}{suffix}", prefix


def hash_api_key(raw: str) -> str:
    return cast(str, bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=12)).decode())


def format_api_key(row: dict[str, Any]) -> dict[str, Any]:
    def isoformat_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return value.replace(tzinfo=timezone.utc).isoformat() if hasattr(value, "replace") else str(value)

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "key_prefix": row["key_prefix"],
        "scope": row["scope"],
        "created_by": row["created_by"],
        "last_used_at": isoformat_or_none(row["last_used_at"]),
        "expires_at": isoformat_or_none(row["expires_at"]),
        "revoked_at": isoformat_or_none(row["revoked_at"]),
        "rotated_from": str(row["rotated_from"]) if row["rotated_from"] else None,
        "created_at": isoformat_or_none(row["created_at"]),
    }
