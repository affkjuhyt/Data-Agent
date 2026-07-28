from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Dict, Optional

PAGE_SIZE = 50


def encode_cursor(row: dict[str, Any], pk: str = "data_id") -> Optional[str]:
    inserted_at = row.get("inserted_at")
    pk_val = row.get(pk)
    if inserted_at is None or pk_val is None:
        return None
    payload = {
        "inserted_at": inserted_at.isoformat() if hasattr(inserted_at, "isoformat") else str(inserted_at),
        "pk": pk,
        "id": str(pk_val),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        ts = payload["inserted_at"]
        try:
            ts = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            pass
        return {"inserted_at": ts, payload["pk"]: payload["id"]}
    except Exception:
        return None


def build_cursor_page(items: list[Any], total: int, has_next: bool, next_cursor: Optional[str]) -> dict[str, Any]:
    return {
        "items": items,
        "page_size": PAGE_SIZE,
        "total": total,
        "has_next": has_next,
        "next_cursor": next_cursor,
    }


_store: Dict[str, Dict[str, Dict[int, dict[str, Any]]]] = {}


def get_cursor(user_id: str, cursor_key: str, page: int) -> Optional[dict[str, Any]]:
    if page <= 1:
        return None
    return _store.get(user_id, {}).get(cursor_key, {}).get(page)


def put_cursor(user_id: str, cursor_key: str, next_page: int, cursor_row: dict[str, Any]) -> None:
    _store.setdefault(user_id, {}).setdefault(cursor_key, {})[next_page] = cursor_row


def advance(user_id: str, cursor_key: str, current_page: int, raw_rows: list[Any]) -> None:
    if len(raw_rows) < PAGE_SIZE:
        return
    last = raw_rows[-1]
    if not isinstance(last, dict) or last.get("inserted_at") is None:
        return
    put_cursor(user_id, cursor_key, current_page + 1, last)


def build_page(items: list[Any], page: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": PAGE_SIZE,
        "has_next": len(items) == PAGE_SIZE,
    }
