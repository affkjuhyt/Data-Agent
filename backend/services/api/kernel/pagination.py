"""Cursor lists (A1.4): ?page_size= (default 50, max 500), ?page_token= and ?order_by=,
answered as {"items": [...], "next_page_token": "..."}; an empty token means
the end.

The token is opaque to clients but only a position (the sort key of the last
row served), base64url JSON. It needs no signature: a forged token can only
move the caller's own cursor, and RLS still scopes every row it reaches.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, tuple_

from services.api.kernel.problems import FieldError, Problem

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_page_token: str = ""


class PageRequest(BaseModel):
    page_size: int
    cursor: dict[str, Any] | None


def encode_page_token(cursor: dict[str, Any] | None) -> str:
    if not cursor:
        return ""
    raw = json.dumps(cursor, separators=(",", ":"), sort_keys=True, default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_page_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        cursor = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise invalid_page_token() from exc
    if not isinstance(cursor, dict):
        raise invalid_page_token()
    return cursor


def invalid_page_token() -> Problem:
    return Problem(
        400,
        "invalid_request",
        "page_token is not a token this API issued.",
        errors=[FieldError(field="page_token", code="invalid", message="Unreadable page token.")],
    )


def page_request(
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    page_token: Annotated[str, Query(max_length=2048)] = "",
    cursor: Annotated[
        str | None, Query(max_length=2048, description="A3's name for page_token")
    ] = None,
) -> PageRequest:
    """A1.4 paging. A3 names the token `cursor` on several lists; both names are accepted."""
    return PageRequest(page_size=page_size, cursor=decode_page_token(cursor or page_token))


# A1.4 sorting: ?order_by=<field> [asc|desc]. A list names the fields it sorts on (non-null
# columns only: keyset paging cannot step over NULLs), and keyset() pages in that order with the
# row id as tiebreak. The token remembers the order it was issued for, so a page_token reused with
# another order_by is refused instead of skipping or repeating rows.


@dataclass(frozen=True)
class Order:
    field: str
    descending: bool

    @property
    def text(self) -> str:
        return f"{self.field} {'desc' if self.descending else 'asc'}"


def order_by(*allowed: str, default: str) -> Any:
    """Dependency for ?order_by on one list: `order: Annotated[Order, Depends(order_by(...))]`."""
    field, _, direction = default.partition(" ")
    fallback = Order(field, direction.strip().lower() != "asc")

    def parse(
        order_by: Annotated[str | None, Query(max_length=64, pattern=_ORDER_PATTERN)] = None,
    ) -> Order:
        if not order_by:
            return fallback
        name, _, way = order_by.strip().partition(" ")
        if name not in allowed:
            raise Problem(
                422,
                "validation_failed",
                f"order_by sorts on one of: {', '.join(allowed)}.",
                errors=[
                    FieldError(field="order_by", code="invalid", message="Not a sortable field.")
                ],
            )
        return Order(name, way.strip().lower() == "desc")

    return parse


_ORDER_PATTERN = r"^[a-z_]+( (asc|desc|ASC|DESC))?$"


def _decoded(value: Any, column: Any) -> Any:
    kind = column.type.python_type
    if kind is datetime.datetime:
        return datetime.datetime.fromisoformat(str(value))
    if kind is datetime.date:
        return datetime.date.fromisoformat(str(value))
    if kind is uuid.UUID:
        return uuid.UUID(str(value))
    return kind(value)


def keyset(
    query: Select[Any],
    *,
    columns: Mapping[str, Any],
    tiebreak: Any,
    order: Order,
    page: PageRequest,
) -> Select[Any]:
    """`query` ordered by `order` (then `tiebreak`), after the page_token's row, one row over the
    page so the caller can tell whether there is a next page (next_page_token())."""
    column = columns[order.field]
    if page.cursor:
        try:
            if page.cursor["o"] != order.text:
                raise ValueError("token issued for another order")
            after = (_decoded(page.cursor["v"], column), _decoded(page.cursor["id"], tiebreak))
        except (KeyError, ValueError, TypeError) as exc:
            raise invalid_page_token() from exc
        position = tuple_(column, tiebreak)
        query = query.where(position < after if order.descending else position > after)
    if order.descending:
        query = query.order_by(column.desc(), tiebreak.desc())
    else:
        query = query.order_by(column.asc(), tiebreak.asc())
    return query.limit(page.page_size + 1)


def next_page(
    rows: list[Any], *, order: Order, page: PageRequest, id_of: str = "id"
) -> tuple[list[Any], str]:
    """The page's rows and the token of the next one ("" at the end)."""
    if len(rows) <= page.page_size:
        return rows, ""
    rows = rows[: page.page_size]
    last = rows[-1]
    return rows, encode_page_token(
        {"o": order.text, "v": getattr(last, order.field), "id": getattr(last, id_of)}
    )
