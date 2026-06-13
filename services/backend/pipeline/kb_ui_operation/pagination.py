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

import datetime
from typing import Dict, Optional

PAGE_SIZE = 50

# _store[user_id][cursor_key][page_index] = cursor_ts
# page_index N means: "this is the inserted_at value at the start of page N"
# so page 1 needs no cursor (always starts from beginning),
# page 2 starts at _store[uid][key][2], etc.
_store: Dict[str, Dict[str, Dict[int, datetime.datetime]]] = {}


def get_cursor(user_id: str, cursor_key: str, page: int) -> Optional[datetime.datetime]:
    """Return stored cursor for the beginning of `page`. None if page 1 or not yet stored."""
    if page <= 1:
        return None
    return _store.get(user_id, {}).get(cursor_key, {}).get(page)


def put_cursor(user_id: str, cursor_key: str, next_page: int, cursor_ts: datetime.datetime) -> None:
    """Store cursor_ts as the start-of-page marker for `next_page`."""
    _store.setdefault(user_id, {}).setdefault(cursor_key, {})[next_page] = cursor_ts


def clear_cursors(user_id: str, cursor_key: str) -> None:
    """Invalidate all stored pages for this user+key (call when filters change)."""
    _store.get(user_id, {}).pop(cursor_key, None)


def advance(user_id: str, cursor_key: str, current_page: int, raw_rows: list) -> None:
    """Extract inserted_at from last raw row and store cursor for next page."""
    if len(raw_rows) < PAGE_SIZE:
        return
    last = raw_rows[-1]
    ts = last.get("inserted_at")
    if ts is None:
        return
    if isinstance(ts, str):
        try:
            ts = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return
    put_cursor(user_id, cursor_key, current_page + 1, ts)


def build_page(items: list, page: int) -> dict:
    return {
        "items":     items,
        "page":      page,
        "page_size": PAGE_SIZE,
        "has_next":  len(items) == PAGE_SIZE,
    }
