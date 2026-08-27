import structlog
from fastapi import Depends, FastAPI

from auth import get_current_user
from clients.s3 import ensure_bucket, s3
from config import BUCKET_ARTIFACTS, BUCKET_AUDIT
from routers import audit_archive, buckets, health, objects, snapshots
from libs.shared.observability import setup_observability

log = structlog.get_logger()


def create_app() -> FastAPI:
    app = FastAPI(title="minio-service", docs_url="/api/storage/docs")
    setup_observability("minio-service", app)

    # /health stays open — the compose healthcheck runs before any token
    # exists and it returns nothing but a literal.
    app.include_router(health.router)

    # Everything else is authenticated at include time. This service was
    # published on host port 8400 with no authentication at all, offering
    # object GET/PUT/DELETE over the artifacts bucket and, worse, per-tenant
    # audit archives: `GET /api/storage/audit-archive/{tenant_id}` read any
    # tenant's exported audit log, and the matching PUT overwrote it. An audit
    # log an unauthenticated caller can rewrite is not an audit log.
    authed = [Depends(get_current_user)]
    for r in (buckets.router, objects.router, snapshots.router, audit_archive.router):
        app.include_router(r, dependencies=authed)

    @app.on_event("startup")
    def init_buckets() -> None:
        client = s3()
        for bucket in (BUCKET_ARTIFACTS, BUCKET_AUDIT):
            ensure_bucket(client, bucket)

    return app
