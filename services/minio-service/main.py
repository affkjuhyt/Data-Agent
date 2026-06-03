"""
MinIO Service — AeroFlow Platform
==================================
S3-compatible object storage API wrapping MinIO.
Handles:
  - Bucket management (create, list)
  - Object CRUD (upload, download, list, delete, head)
  - Release snapshots: aeroflow-artifacts/rollback-snapshots/<env>-<version>.tar.gz
  - Audit log archival: aeroflow-audit-archive/<tenant>/<year>/<month>.jsonl
"""
import os
from typing import Optional

import boto3
import structlog
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

structlog.configure(processors=[structlog.processors.JSONRenderer()])
log = structlog.get_logger()

MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT",   "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio_secret")

BUCKET_ARTIFACTS = "aeroflow-artifacts"
BUCKET_AUDIT     = "aeroflow-audit-archive"

app = FastAPI(title="minio-service", docs_url="/api/storage/docs")


def s3():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def _ensure_bucket(client, bucket: str):
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=bucket)
            log.info("bucket.created", bucket=bucket)
        else:
            raise


@app.on_event("startup")
def _init_buckets():
    client = s3()
    for bucket in (BUCKET_ARTIFACTS, BUCKET_AUDIT):
        _ensure_bucket(client, bucket)


# ── Health ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        s3().list_buckets()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Buckets ───────────────────────────────────────────────────────────

@app.get("/api/storage/buckets")
def list_buckets():
    resp = s3().list_buckets()
    return {"buckets": [b["Name"] for b in resp.get("Buckets", [])]}


class BucketCreate(BaseModel):
    name: str


@app.post("/api/storage/buckets", status_code=201)
def create_bucket(body: BucketCreate):
    client = s3()
    try:
        client.head_bucket(Bucket=body.name)
        return {"bucket": body.name, "created": False}
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise HTTPException(status_code=500, detail=str(e))
    client.create_bucket(Bucket=body.name)
    log.info("bucket.created", bucket=body.name)
    return {"bucket": body.name, "created": True}


# ── Objects ───────────────────────────────────────────────────────────

@app.get("/api/storage/buckets/{bucket}/objects")
def list_objects(bucket: str, prefix: Optional[str] = Query(default="")):
    try:
        resp = s3().list_objects_v2(Bucket=bucket, Prefix=prefix or "")
    except ClientError as e:
        raise HTTPException(status_code=404, detail=str(e))
    objects = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"bucket": bucket, "objects": objects}


@app.head("/api/storage/buckets/{bucket}/objects/{key:path}")
def head_object(bucket: str, key: str):
    try:
        resp = s3().head_object(Bucket=bucket, Key=key)
        return {"content_length": resp["ContentLength"], "last_modified": resp["LastModified"].isoformat()}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))


@app.put("/api/storage/buckets/{bucket}/objects/{key:path}", status_code=201)
def upload_object(bucket: str, key: str, file: UploadFile = File(...)):
    try:
        s3().upload_fileobj(file.file, bucket, key)
        log.info("object.uploaded", bucket=bucket, key=key)
        return {"bucket": bucket, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/storage/buckets/{bucket}/objects/{key:path}")
def download_object(bucket: str, key: str):
    try:
        resp = s3().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))

    def _stream():
        for chunk in resp["Body"].iter_chunks(chunk_size=8192):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type=resp.get("ContentType", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{key.split("/")[-1]}"'},
    )


@app.delete("/api/storage/buckets/{bucket}/objects/{key:path}", status_code=204)
def delete_object(bucket: str, key: str):
    try:
        s3().delete_object(Bucket=bucket, Key=key)
        log.info("object.deleted", bucket=bucket, key=key)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Snapshots (release rollback) ──────────────────────────────────────

@app.head("/api/storage/snapshots/{environment}/{version}")
def head_snapshot(environment: str, version: str):
    key = f"rollback-snapshots/{environment}-{version}.tar.gz"
    try:
        resp = s3().head_object(Bucket=BUCKET_ARTIFACTS, Key=key)
        return {
            "exists": True,
            "key": key,
            "content_length": resp["ContentLength"],
            "last_modified": resp["LastModified"].isoformat(),
        }
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            raise HTTPException(status_code=404, detail="snapshot not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/storage/snapshots/{environment}/{version}", status_code=201)
def upload_snapshot(environment: str, version: str, file: UploadFile = File(...)):
    key = f"rollback-snapshots/{environment}-{version}.tar.gz"
    try:
        s3().upload_fileobj(file.file, BUCKET_ARTIFACTS, key)
        log.info("snapshot.uploaded", environment=environment, version=version, key=key)
        return {"bucket": BUCKET_ARTIFACTS, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/storage/snapshots/{environment}/{version}")
def download_snapshot(environment: str, version: str):
    key = f"rollback-snapshots/{environment}-{version}.tar.gz"
    try:
        resp = s3().get_object(Bucket=BUCKET_ARTIFACTS, Key=key)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))

    def _stream():
        for chunk in resp["Body"].iter_chunks(chunk_size=8192):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{environment}-{version}.tar.gz"'},
    )


@app.get("/api/storage/snapshots")
def list_snapshots(environment: Optional[str] = Query(default=None)):
    prefix = f"rollback-snapshots/{environment}-" if environment else "rollback-snapshots/"
    resp = s3().list_objects_v2(Bucket=BUCKET_ARTIFACTS, Prefix=prefix)
    snapshots = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"snapshots": snapshots}


# ── Audit Archive ─────────────────────────────────────────────────────

@app.put("/api/storage/audit-archive/{tenant_id}/{year}/{month}", status_code=201)
def upload_audit_archive(tenant_id: str, year: int, month: int, file: UploadFile = File(...)):
    key = f"{tenant_id}/{year}/{month:02d}.jsonl"
    try:
        s3().upload_fileobj(file.file, BUCKET_AUDIT, key)
        log.info("audit.archive.uploaded", tenant_id=tenant_id, year=year, month=month, key=key)
        return {"bucket": BUCKET_AUDIT, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/storage/audit-archive/{tenant_id}")
def list_audit_archives(tenant_id: str):
    resp = s3().list_objects_v2(Bucket=BUCKET_AUDIT, Prefix=f"{tenant_id}/")
    archives = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"tenant_id": tenant_id, "archives": archives}
