"""Best-effort object storage for submitted sandbox code (docs/1.md
"Kế hoạch 1" mục h).

Reuses the platform's existing MinIO instance instead of standing up new
infrastructure — same endpoint/credential env var convention already used
by services/minio-service and libs/database_connector/minio_connector
(MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY), just a new bucket
(`sandbox-code`, configurable). sandbox-executor is a small, deliberately
self-contained service (its Dockerfile COPYs only its own .py files, no
shared services.* packages) — a direct boto3 client here mirrors that,
rather than pulling in libs/database_connector's aioboto3 wrapper or
routing through minio-service's internal HTTP API.

This is an audit/observability aid, not a security control — the AST
whitelist (security_visitor.py) and the container's own isolation are
what actually keep execution safe. A MinIO outage must never block or
fail a sandbox request, so every call here is best-effort: log a warning
and continue.

"Best-effort" must mean fast-fail, not just non-crashing: boto3's
default connect/read timeouts and retry policy add up to several
retried, multi-second attempts before giving up on an unreachable
endpoint (measured against a genuinely down endpoint: ~9s for a single
put_object with library defaults). put_code()/delete_code() sit directly
on the request path (main.py awaits them via asyncio.to_thread before/
after every sandbox exec) — silently adding 9+ seconds of latency to
every single request the moment MinIO is unavailable would make "doesn't
block execution" true in name only. _BOTO_CONFIG below caps that at
worst case ~1-2s (short connect/read timeout, no retries).
"""
import logging
import os
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minio_secret")
BUCKET = os.environ.get("SANDBOX_CODE_BUCKET", "sandbox-code")

_BOTO_CONFIG = Config(
    connect_timeout=float(os.environ.get("SANDBOX_MINIO_CONNECT_TIMEOUT_S", "2")),
    read_timeout=float(os.environ.get("SANDBOX_MINIO_READ_TIMEOUT_S", "2")),
    retries={"max_attempts": 1},  # fail fast — see module docstring
)


_client_instance: Any = None


def _client() -> Any:
    # boto3 clients are thread-safe and cheap to reuse — building one from
    # scratch on every put_code/delete_code/ensure_bucket call (this sits on
    # the hot request path, see module docstring) adds needless connection
    # setup overhead for no benefit.
    global _client_instance
    if _client_instance is None:
        _client_instance = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=_BOTO_CONFIG,
        )
    return _client_instance


def ensure_bucket() -> None:
    """Call once at startup (main.py's lifespan), via asyncio.to_thread —
    blocking network I/O. Failure here must not stop sandbox-executor from
    serving requests: code execution does not depend on this bucket."""
    try:
        client = _client()
        try:
            client.head_bucket(Bucket=BUCKET)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchBucket"):
                client.create_bucket(Bucket=BUCKET)
                logger.info("sandbox-executor | minio bucket created: %s", BUCKET)
            else:
                raise
    except Exception as e:
        logger.warning(
            "sandbox-executor | minio unavailable at startup — code audit-copy disabled, "
            "executions are unaffected: %s", e,
        )


def put_code(key: str, code: str) -> None:
    """Best-effort — never raises. Call via asyncio.to_thread."""
    try:
        _client().put_object(Bucket=BUCKET, Key=key, Body=code.encode("utf-8"), ContentType="text/plain")
    except Exception as e:
        logger.warning("sandbox-executor | minio upload failed for %s (continuing without audit copy): %s", key, e)


def delete_code(key: str) -> None:
    """Best-effort — never raises. Call via asyncio.to_thread. Always
    called from the same finally-style cleanup branch as the container's
    own kill+wipe (docs/1.md mục c/h), regardless of success/error/timeout."""
    try:
        _client().delete_object(Bucket=BUCKET, Key=key)
    except Exception as e:
        logger.warning("sandbox-executor | minio delete failed for %s: %s", key, e)
