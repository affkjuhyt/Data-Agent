"""Object-storage browsing for the admin Settings page (/api/storage/*).

Folded in from the former minio_service, which only ever had one caller: the
Settings > Storage panel (frontend/src/lib/usePlatformData.ts) listing buckets
and the objects under one. The rest of that service — bucket create, object
upload/download/delete, snapshots, per-tenant audit archives — had no callers
and is gone with it; add a route here only when something actually calls it.

Authentication is attached at include time in app.py (same `authed` loop as
every other router). The old service scoped nothing by tenant: MinIO buckets
are platform-wide infrastructure and the panel is admin-only, so this keeps the
same platform-wide listing.

boto3 is synchronous; calls run under asyncio.to_thread so a slow MinIO does
not block the event loop that also serves agent turns.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/storage")


def s3_client() -> Any:
    """Build an S3 client from MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY.

    Same variable names the former minio_service and sandbox_executor use, so
    the compose block is a copy, not a translation. Read at call time so a test
    can monkeypatch the environment (or this function) without reloading.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio:9000"),
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minio"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minio_secret"),
    )


@router.get("/buckets")
async def list_buckets() -> dict[str, Any]:
    """{"buckets": [name, ...]} — the shape usePlatformBuckets() expects."""
    try:
        resp = await asyncio.to_thread(lambda: s3_client().list_buckets())
    except (ClientError, BotoCoreError) as e:
        raise HTTPException(status_code=503, detail=f"object storage unavailable: {e}")
    return {"buckets": [b["Name"] for b in resp.get("Buckets", [])]}


@router.get("/buckets/{bucket}/objects")
async def list_objects(bucket: str, prefix: str | None = Query(default="")) -> dict[str, Any]:
    """{"bucket", "objects": [{key, size, last_modified}]} — usePlatformBucketObjects()."""
    try:
        resp = await asyncio.to_thread(
            lambda: s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix or "")
        )
    except ClientError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BotoCoreError as e:
        raise HTTPException(status_code=503, detail=f"object storage unavailable: {e}")
    objects = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"bucket": bucket, "objects": objects}
