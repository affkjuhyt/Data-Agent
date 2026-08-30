import ipaddress
from typing import Any


def validate_cidr(cidr: str) -> None:
    ipaddress.ip_network(cidr, strict=False)


def format_ip_allowlist(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "cidr": row["cidr"],
        "label": row["label"] or "",
        "is_active": row["is_active"],
        "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
