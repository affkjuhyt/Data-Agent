from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from libs.shared.auth import require_tenant
from services.workflow_runtime.application.answer_exports import load_payload, load_xlsx
from services.workflow_runtime.api.auth import get_current_user

router = APIRouter(prefix="/api/ask/results")


def _sort_value(row: dict[str, Any], column: str) -> tuple[bool, bool, float | str]:
    value = row.get(column)
    if value is None:
        return True, False, ""
    try:
        return False, True, float(value)
    except (TypeError, ValueError):
        return False, False, str(value).casefold()


@router.get("/{result_id}/rows")
async def result_rows(
    result_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    sort: str | None = None,
    dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    try:
        payload = await load_payload(tenant_id, result_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid result id")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="result export not found")

    rows = [r for r in payload.get("rows", []) if isinstance(r, dict)]
    columns = [str(c) for c in payload.get("columns", [])]
    if sort and sort in columns:
        rows = sorted(rows, key=lambda r: _sort_value(r, sort), reverse=dir == "desc")

    page = rows[offset:offset + limit]
    return {
        "result_id": result_id,
        "columns": columns,
        "rows": page,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(rows),
    }


@router.get("/{result_id}/export.xlsx")
async def result_xlsx(
    result_id: str,
    caller: dict[str, Any] = Depends(get_current_user),
) -> Response:
    tenant_id = require_tenant(caller)
    try:
        data = await load_xlsx(tenant_id, result_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid result id")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="result export not found")

    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="ask-result-{result_id[:8]}.xlsx"'},
    )
