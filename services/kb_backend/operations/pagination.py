"""
Cursor-based pagination helpers.

Cursors are stored in-memory per (user_id, cursor_key, page_index).
On server restart the store is empty — clients that request page > 1
without a stored cursor fall back to offset-based estimation (not ideal,
but acceptable for a dev environment).

cursor_key examples:
  "docs:bronze", "docs:silver", "docs:gold", "docs:all"
  "chunks:{doc_id}", "tables:{doc_id}"
  "conflicts:pending", "conflicts:awaiting", "conflicts:resolved"
  "policies:filter:{tenant_id}"
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Dict, Optional

PAGE_SIZE = 50


# ── Stateless cursor (client-held) ────────────────────────────────────────────
# The server NEVER stores these. It encodes the keyset boundary (the last row of
# the page — its inserted_at + data_id) into an opaque base64 token and returns it
# as `next_cursor`. The client holds the token and sends it back to fetch the next
# page. Decoding yields the dict the connector's _apply_keyset expects.

def encode_cursor(row: dict, pk: str = "data_id") -> Optional[str]:
    """base64(JSON) of the keyset boundary. None if the row can't anchor a cursor."""
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


def decode_cursor(token: Optional[str]) -> Optional[dict]:
    """Reverse encode_cursor → {inserted_at, <pk>} for the connector keyset, or None."""
    if not token:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        # Parse inserted_at back to a datetime so the driver binds it as timestamptz
        # (a str binds as varchar → "operator does not exist: timestamptz > varchar").
        ts = payload["inserted_at"]
        try:
            ts = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            pass
        return {"inserted_at": ts, payload["pk"]: payload["id"]}
    except Exception:
        return None


def build_cursor_page(items: list, total: int, has_next: bool, next_cursor: Optional[str]) -> dict:
    """Page envelope for stateless cursor pagination: carries total + next_cursor.
    `next_cursor` is encoded by the caller from the raw DB row (which still has the
    inserted_at/data_id keyset columns — the response DTO usually drops them)."""
    return {
        "items":       items,
        "page_size":   PAGE_SIZE,
        "total":       total,
        "has_next":    has_next,
        "next_cursor": next_cursor,
    }

# _store[user_id][cursor_key][page] = the LAST ROW of the previous page (a dict).
# page 1 needs no cursor (always starts from the beginning); page N (N>1) resumes
# after _store[uid][key][N] using a keyset (inserted_at, pk) comparison. The whole
# row is kept so the connector — which knows each table's pk — can read the
# tiebreaker back out. Storing only inserted_at skipped rows tied on that timestamp.
_store: Dict[str, Dict[str, Dict[int, dict]]] = {}


def get_cursor(user_id: str, cursor_key: str, page: int) -> Optional[dict]:
    """Return the prior page's last-row cursor for `page`. None if page 1 or unset."""
    if page <= 1:
        return None
    return _store.get(user_id, {}).get(cursor_key, {}).get(page)


def put_cursor(user_id: str, cursor_key: str, next_page: int, cursor_row: dict) -> None:
    """Store the last-row cursor that marks the start of `next_page`."""
    _store.setdefault(user_id, {}).setdefault(cursor_key, {})[next_page] = cursor_row


def advance(user_id: str, cursor_key: str, current_page: int, raw_rows: list) -> None:
    """Store the LAST row of this page as the keyset cursor for the next page.

    Keyset resumes with WHERE (inserted_at, pk) > (cursor.inserted_at, cursor.pk),
    so the cursor must carry the whole row — the connector reads the pk back out.
    Only stores when the page was full (a partial page has no next page)."""
    if len(raw_rows) < PAGE_SIZE:
        return
    last = raw_rows[-1]
    if not isinstance(last, dict) or last.get("inserted_at") is None:
        return
    put_cursor(user_id, cursor_key, current_page + 1, last)


def build_page(items: list, page: int) -> dict:
    return {
        "items":     items,
        "page":      page,
        "page_size": PAGE_SIZE,
        "has_next":  len(items) == PAGE_SIZE,
    }
