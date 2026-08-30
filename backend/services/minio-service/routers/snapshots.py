from collections.abc import Iterator
from typing import Any, Optional

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from clients.s3 import s3
from config import BUCKET_ARTIFACTS

log = structlog.get_logger()
router = APIRouter(prefix="/api/storage/snapshots")


@router.head("/{environment}/{version}")
def head_snapshot(environment: str, version: str) -> dict[str, Any]:
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


@router.put("/{environment}/{version}", status_code=201)
def upload_snapshot(environment: str, version: str, file: UploadFile = File(...)) -> dict[str, Any]:
    key = f"rollback-snapshots/{environment}-{version}.tar.gz"
    try:
        s3().upload_fileobj(file.file, BUCKET_ARTIFACTS, key)
        log.info("snapshot.uploaded", environment=environment, version=version, key=key)
        return {"bucket": BUCKET_ARTIFACTS, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{environment}/{version}")
def download_snapshot(environment: str, version: str) -> StreamingResponse:
    key = f"rollback-snapshots/{environment}-{version}.tar.gz"
    try:
        resp = s3().get_object(Bucket=BUCKET_ARTIFACTS, Key=key)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))

    def stream() -> Iterator[bytes]:
        for chunk in resp["Body"].iter_chunks(chunk_size=8192):
            yield chunk

    return StreamingResponse(
        stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{environment}-{version}.tar.gz"'},
    )


@router.get("")
def list_snapshots(environment: Optional[str] = Query(default=None)) -> dict[str, Any]:
    prefix = f"rollback-snapshots/{environment}-" if environment else "rollback-snapshots/"
    resp = s3().list_objects_v2(Bucket=BUCKET_ARTIFACTS, Prefix=prefix)
    snapshots = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"snapshots": snapshots}
