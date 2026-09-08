"""Optimistic locking: SELECT ... FOR UPDATE, verify row_version, then update."""
import uuid
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import func, select, update as sa_update


def parse_if_match(value: str | None) -> uuid.UUID | None:
    """None when the header is absent; 400 when it is present but malformed."""
    token = (value or "").strip().strip('"').strip()
    if not token:
        return None
    try:
        return uuid.UUID(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed If-Match header")


def require_if_match(value: str | None) -> uuid.UUID:
    """428 when the header is absent — every update must state the version it edits."""
    parsed = parse_if_match(value)
    if parsed is None:
        raise HTTPException(
            status_code=428,
            detail="If-Match header is required — GET the resource and resend its row_version",
        )
    return parsed


def etag(row_version: Any) -> str:
    return f'"{row_version}"'


async def update_locked(
        postgres: Any,
        orm: Any,
        conditions: list[Any],
        mutate: Callable[[Any], dict[str, Any]],
        expected_version: uuid.UUID | None = None,
        not_found_detail: str = "Not found",
        tenant_id: str | None = None,
) -> uuid.UUID:
    """Lock the row, confirm it exists and is unchanged, then write.

    `mutate` receives the locked row and returns the columns to set, so a
    read-modify-write sees the same snapshot the lock protects.
    Raises 404 if the row is gone, 412 if `expected_version` no longer matches.
    """
    async with postgres.get_client(tenant_id=tenant_id) as session:
        try:
            row = (await session.execute(
                select(orm).where(*conditions).with_for_update()
            )).scalar_one_or_none()

            if row is None:
                await session.rollback()
                raise HTTPException(status_code=404, detail=not_found_detail)

            if expected_version is not None and row.row_version != expected_version:
                await session.rollback()
                raise HTTPException(
                    status_code=412,
                    detail="Resource was modified by someone else — reload and retry",
                )

            values = mutate(row)
            new_version = uuid.uuid4()
            await session.execute(
                sa_update(orm)
                .where(*conditions)
                .values(**values, row_version=new_version, updated_at=func.now())
            )
            await session.commit()
            return new_version
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise
