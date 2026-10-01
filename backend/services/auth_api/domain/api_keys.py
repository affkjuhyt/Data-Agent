import secrets
import string
from datetime import datetime, timezone
from typing import Any, Optional, cast

import bcrypt

SCOPE_PREFIX = {"full_access": "prod", "read_only": "ro", "admin_platform": "adm"}
VALID_SCOPES = set(SCOPE_PREFIX.keys())

# The role a key acts as once verified. Only platform-admin can issue keys, so
# no scope reaches past what its issuer already had.
SCOPE_ROLES = {
    "full_access": "ai-engineer",
    "read_only": "executive-viewer",
    "admin_platform": "platform-admin",
}

# A key is its stored key_prefix followed by this many random characters.
KEY_SUFFIX_LENGTH = 32


def generate_api_key(scope: str) -> tuple[str, str]:
    suffix = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(KEY_SUFFIX_LENGTH))
    prefix = f"sk-{SCOPE_PREFIX.get(scope, 'key')}-{''.join(secrets.choice(string.ascii_letters) for _ in range(4))}"
    return f"{prefix}{suffix}", prefix


def hash_api_key(raw: str) -> str:
    return cast(str, bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=12)).decode())


def key_prefix_of(raw: str) -> str | None:
    """The key_prefix a presented key was issued under, or None if it cannot be one of ours."""
    if not raw.startswith("sk-") or len(raw) <= KEY_SUFFIX_LENGTH:
        return None
    return raw[:-KEY_SUFFIX_LENGTH]


def api_key_matches(raw: str, key_hash: str) -> bool:
    return bool(bcrypt.checkpw(raw.encode(), key_hash.encode()))


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
