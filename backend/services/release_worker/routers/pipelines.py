from typing import Any
from fastapi import APIRouter
from psycopg2.extras import RealDictCursor

from services.release_worker.release_core import *
from services.release_worker.schemas import *

router = APIRouter()


@router.post("/api/release/pipeline/trigger")
def trigger_pipeline(
    req: TriggerPipelineRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Trigger pipeline thủ công — produce Kafka event."""
    pipeline_id = f"pipe-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    event = {
        "event": "pipeline.triggered",
        "pipeline_id": pipeline_id,
        "triggered_by": user["user_id"],
        "trigger_type": "MANUAL",
        "commit_sha": req.commit_sha,
        "branch": req.branch,
        "package_version": req.package_version,
        "target_environments": req.target_environments,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    produce_event("release.pipeline.triggered", event)
    log.info("api.pipeline.triggered", pipeline_id=pipeline_id, user=user["user_id"])
    return {"pipeline_id": pipeline_id, "status": "triggered"}


@router.post("/api/release/pipeline/{pipeline_id}/approve/{environment}")
def approve_deployment(
    pipeline_id: str,
    environment: str,
    req: ApprovalRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Approval / Reject deployment lên staging / production."""
    if "platform-admin" not in user["roles"] and "ai-engineer" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Insufficient role for approval")

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            # Lấy package_id từ pipeline
            cur.execute('SELECT id FROM "ReleasePackages" WHERE pipeline_id=%s LIMIT 1',
                        (pipeline_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Package not found")
            package_id = row[0]

            cur.execute(
                """INSERT INTO "ReleaseApprovals"
                   (package_id, environment, decision, approved_by, comment)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (package_id, environment, approved_by) DO UPDATE
                   SET decision=%s, approved_at=now()""",
                (package_id, environment, req.decision, user["user_id"], req.comment,
                 req.decision)
            )

    if req.decision == "APPROVED":
        # Deploy in background thread — skip build/scan (already done)
        t = threading.Thread(
            target=_run_approval_deploy,
            args=(pipeline_id, environment, user["user_id"]),
            daemon=True
        )
        t.start()
        log.info("api.approval.approved", pipeline_id=pipeline_id,
                 environment=environment, approver=user["user_id"])
        return {"status": "approved", "pipeline_id": pipeline_id, "environment": environment}
    else:
        update_pipeline_status(pipeline_id, "FAILED", error=f"Rejected by {user['user_id']}")
        return {"status": "rejected", "pipeline_id": pipeline_id}


@router.post("/api/release/rollback")
def trigger_rollback(
    req: RollbackRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Trigger rollback — produce Kafka event; the consumer thread runs it."""
    if "platform-admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only platform-admin can trigger rollback")

    rollback_id = f"rb-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    event = {
        "event": "rollback.initiated",
        "rollback_id": rollback_id,
        "from_version": req.from_version,
        "to_version": req.to_version,
        "environment": req.environment,
        "triggered_by": user["user_id"],
        "reason": req.reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    produce_event("release.rollback.initiated", event)
    # The Kafka consumer (start_kafka_consumer) picks up release.rollback.initiated
    # and calls _run_rollback_impl in a thread — no separate .delay() needed here.
    log.info("api.rollback.triggered", rollback_id=rollback_id, user=user["user_id"])
    return {"rollback_id": rollback_id, "status": "initiated"}


@router.get("/api/release/pipelines")
def list_pipelines(
    status: Optional[str] = None,
    limit: int = 50,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """List tất cả pipelines — cho UI Deployments tab."""
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status:
                cur.execute(
                    """SELECT id, pipeline_name, triggered_by, trigger_type, commit_sha,
                              branch, package_version, target_env, status, risk_score,
                              error_message, created_at, updated_at, completed_at
                       FROM "Pipelines"
                       WHERE status = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (status, limit)
                )
            else:
                cur.execute(
                    """SELECT id, pipeline_name, triggered_by, trigger_type, commit_sha,
                              branch, package_version, target_env, status, risk_score,
                              error_message, created_at, updated_at, completed_at
                       FROM "Pipelines"
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,)
                )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                for k in ("created_at", "updated_at", "completed_at"):
                    if row.get(k):
                        row[k] = row[k].isoformat()
    return {"pipelines": rows, "count": len(rows)}


@router.get("/api/release/rollback-targets")
def list_rollback_targets(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Trả danh sách pipeline có thể rollback: lấy 2 lần deploy gần nhất mỗi (name, env)
    từ release_history, tính current vs previous version.
    """
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """WITH history_with_name AS (
                     SELECT
                       rh.pipeline_id,
                       COALESCE(p.pipeline_name, rh.pipeline_id) AS name,
                       rh.environment,
                       rh.status,
                       p.package_version,
                       rh.deployed_at,
                       ROW_NUMBER() OVER (
                         PARTITION BY COALESCE(p.pipeline_name, rh.pipeline_id), rh.environment
                         ORDER BY rh.deployed_at DESC
                       ) AS rn
                     FROM "ReleaseHistory" rh
                     JOIN "Pipelines" p ON p.id = rh.pipeline_id
                     WHERE rh.status IN ('SUCCESS', 'ROLLED_BACK')
                   ),
                   current_v AS (
                     SELECT pipeline_id, name, environment, package_version AS current_version
                     FROM history_with_name WHERE rn = 1
                   ),
                   previous_v AS (
                     SELECT name, environment, package_version AS previous_version
                     FROM history_with_name WHERE rn = 2
                   )
                   SELECT c.pipeline_id, c.name, c.current_version, pv.previous_version, c.environment
                   FROM current_v c
                   JOIN previous_v pv ON pv.name = c.name AND pv.environment = c.environment
                   ORDER BY c.name, c.environment"""
            )
            rows = [dict(row) for row in cur.fetchall()]
    return {"targets": rows, "count": len(rows)}


@router.get("/api/release/history")
def get_release_history(
    environment: Optional[str] = None,
    limit: int = 50,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Query release history từ PostgreSQL."""
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if environment:
                cur.execute(
                    """SELECT pipeline_id, package_id, environment, status,
                              triggered_by, deployed_at, duration_ms
                       FROM "ReleaseHistory"
                       WHERE environment=%s
                       ORDER BY deployed_at DESC LIMIT %s""",
                    (environment, limit)
                )
            else:
                cur.execute(
                    """SELECT pipeline_id, package_id, environment, status,
                              triggered_by, deployed_at, duration_ms
                       FROM "ReleaseHistory"
                       ORDER BY deployed_at DESC LIMIT %s""",
                    (limit,)
                )
            rows = [dict(row) for row in cur.fetchall()]
            # Convert datetime to ISO string
            for row in rows:
                if row.get("deployed_at"):
                    row["deployed_at"] = row["deployed_at"].isoformat()
    return {"history": rows, "count": len(rows)}


@router.get("/api/release/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Xem chi tiết pipeline và các steps."""
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT id, triggered_by, status, created_at, updated_at, error_message FROM "Pipelines" WHERE id=%s',
                (pipeline_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Pipeline not found")
            pipeline = dict(row)
            for k in ["created_at", "updated_at"]:
                if pipeline[k]:
                    pipeline[k] = pipeline[k].isoformat()

            cur.execute(
                'SELECT step_name, status, started_at, completed_at, duration_ms, output, error FROM "PipelineSteps" WHERE pipeline_id=%s ORDER BY id',
                (pipeline_id,)
            )
            steps = [dict(s) for s in cur.fetchall()]
    return {"pipeline": pipeline, "steps": steps}
