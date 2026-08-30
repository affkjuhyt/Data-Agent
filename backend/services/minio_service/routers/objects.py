from collections.abc import Iterator
from typing import Any, Optional

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from clients.s3 import s3

log = structlog.get_logger()
router = APIRouter(prefix="/api/storage/buckets/{bucket}/objects")


@router.get("")
def list_objects(bucket: str, prefix: Optional[str] = Query(default="")) -> dict[str, Any]:
    try:
        resp = s3().list_objects_v2(Bucket=bucket, Prefix=prefix or "")
    except ClientError as e:
        raise HTTPException(status_code=404, detail=str(e))
    objects = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"bucket": bucket, "objects": objects}


@router.head("/{key:path}")
def head_object(bucket: str, key: str) -> dict[str, Any]:
    try:
        resp = s3().head_object(Bucket=bucket, Key=key)
        return {"content_length": resp["ContentLength"], "last_modified": resp["LastModified"].isoformat()}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))


@router.put("/{key:path}", status_code=201)
def upload_object(bucket: str, key: str, file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        s3().upload_fileobj(file.file, bucket, key)
        log.info("object.uploaded", bucket=bucket, key=key)
        return {"bucket": bucket, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{key:path}")
def download_object(bucket: str, key: str) -> StreamingResponse:
    try:
        resp = s3().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise HTTPException(status_code=404 if code in ("404", "NoSuchKey") else 500, detail=str(e))

    def stream() -> Iterator[bytes]:
        for chunk in resp["Body"].iter_chunks(chunk_size=8192):
            yield chunk

    return StreamingResponse(
        stream(),
        media_type=resp.get("ContentType", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{key.split("/")[-1]}"'},
    )


@router.delete("/{key:path}", status_code=204)
def delete_object(bucket: str, key: str) -> None:
    try:
        s3().delete_object(Bucket=bucket, Key=key)
        log.info("object.deleted", bucket=bucket, key=key)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
