"""Caller authentication for minio_service.

Thin binding over backend/libs/shared/auth, the same trust decision every other service
makes.

This service was published on host port 8400 with no authentication of any
kind. Beyond object read/write on the artifacts bucket, it exposes per-tenant
audit archives: `GET /api/storage/audit-archive/{tenant_id}` listed any tenant's
exported audit log and the matching `PUT` replaced it. An audit log that an
unauthenticated caller can rewrite does not record anything — it is the record
an investigation would rely on, and the one an attacker would edit first.

MINIO_ALLOWED_SERVICES lists the services that archive and read on a user's
behalf; unlike flow_buider, this one genuinely has service-to-service callers
(the audit export job), so the assertion path stays open to a named allowlist.
"""
from libs.shared.auth import make_get_current_user

get_current_user = make_get_current_user("MINIO_ALLOWED_SERVICES", "audit-consumer,release_worker")
