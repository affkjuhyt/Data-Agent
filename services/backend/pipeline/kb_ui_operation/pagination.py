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

from typing import Dict, Optional

PAGE_SIZE = 50

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


def clear_cursors(user_id: str, cursor_key: str) -> None:
    """Invalidate all stored pages for this user+key (call when filters change)."""
    _store.get(user_id, {}).pop(cursor_key, None)


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
