import structlog
from fastapi import FastAPI

from clients.s3 import ensure_bucket, s3
from config import BUCKET_ARTIFACTS, BUCKET_AUDIT
from routers import audit_archive, buckets, health, objects, snapshots
from shared.observability import setup_observability

log = structlog.get_logger()


def create_app() -> FastAPI:
    app = FastAPI(title="minio-service", docs_url="/api/storage/docs")
    setup_observability("minio-service", app)

    app.include_router(health.router)
    app.include_router(buckets.router)
    app.include_router(objects.router)
    app.include_router(snapshots.router)
    app.include_router(audit_archive.router)

    @app.on_event("startup")
    def init_buckets() -> None:
        client = s3()
        for bucket in (BUCKET_ARTIFACTS, BUCKET_AUDIT):
            ensure_bucket(client, bucket)

    return app
