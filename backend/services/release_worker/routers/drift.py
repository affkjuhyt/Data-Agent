from typing import Any
from fastapi import APIRouter
from psycopg2.extras import RealDictCursor

from services.release_worker.release_core import *
from services.release_worker.schemas import *

router = APIRouter()


@router.post("/api/release/drift/detect")
def api_detect_drift(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Trigger drift detection thủ công — chạy inline trong request.
    Gọi inline _detect_config_drift_core(), ghi DB, produce Kafka event.
    Trả về ngay kết quả: clean hoặc drift_detected với danh sách keys bị drift.
    """
    if "platform-admin" not in user["roles"] and "ai-engineer" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Insufficient role for drift detection")
    try:
        result = _detect_config_drift_core()
        log.info("api.drift.detect.done", status=result["status"], user=user["user_id"])
        return result
    except Exception as e:
        log.error("api.drift.detect.error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/release/drift")
def list_drift_events(
    resolved: Optional[bool] = None,
    limit: int = 20,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """List drift events từ DB — hỗ trợ filter theo resolved status."""
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if resolved is not None:
                cur.execute(
                    """SELECT id, env_pair, drift_keys, severity, detected_at, resolved
                       FROM "DriftEvents"
                       WHERE resolved = %s
                       ORDER BY detected_at DESC LIMIT %s""",
                    (resolved, limit)
                )
            else:
                cur.execute(
                    """SELECT id, env_pair, drift_keys, severity, detected_at, resolved
                       FROM "DriftEvents"
                       ORDER BY detected_at DESC LIMIT %s""",
                    (limit,)
                )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                if row.get("detected_at"):
                    row["detected_at"] = row["detected_at"].isoformat()
                if isinstance(row.get("drift_keys"), str):
                    try:
                        row["drift_keys"] = json.loads(row["drift_keys"])
                    except Exception:
                        pass
    return {"drift_events": rows, "count": len(rows)}


@router.post("/api/release/drift/resolve/{drift_id}")
def resolve_drift_event(
    drift_id: int,
    req: DriftResolveRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark drift event as resolved sau khi team đã sync config."""
    if "platform-admin" not in user["roles"] and "ai-engineer" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Insufficient role for drift resolution")
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE "DriftEvents" SET resolved = true WHERE id = %s',
                (drift_id,)
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Drift event not found")
    log.info("api.drift.resolved", drift_id=drift_id, resolved_by=user["user_id"],
             notes=req.notes)
    return {"status": "resolved", "drift_id": drift_id, "resolved_by": user["user_id"]}


# ═══════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════
