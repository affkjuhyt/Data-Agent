"""Cursor lists (A1.4): ?page_size= (default 50, max 500) and ?page_token=,
answered as {"items": [...], "next_page_token": "..."}; an empty token means
the end.

The token is opaque to clients but only a position (the sort key of the last
row served), base64url JSON. It needs no signature: a forged token can only
move the caller's own cursor, and RLS still scopes every row it reaches.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

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
        raise _bad_token() from exc
    if not isinstance(cursor, dict):
        raise _bad_token()
    return cursor


def _bad_token() -> Problem:
    return Problem(
        400,
        "invalid_request",
        "page_token is not a token this API issued.",
        errors=[FieldError(field="page_token", code="invalid", message="Unreadable page token.")],
    )


def page_request(
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    page_token: Annotated[str, Query(max_length=2048)] = "",
) -> PageRequest:
    return PageRequest(page_size=page_size, cursor=decode_page_token(page_token))
