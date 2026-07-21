from typing import Any

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, File, HTTPException, UploadFile

from clients.s3 import s3
from config import BUCKET_AUDIT

log = structlog.get_logger()
router = APIRouter(prefix="/api/storage/audit-archive")


@router.put("/{tenant_id}/{year}/{month}", status_code=201)
def upload_audit_archive(tenant_id: str, year: int, month: int, file: UploadFile = File(...)) -> dict[str, Any]:
    key = f"{tenant_id}/{year}/{month:02d}.jsonl"
    try:
        s3().upload_fileobj(file.file, BUCKET_AUDIT, key)
        log.info("audit.archive.uploaded", tenant_id=tenant_id, year=year, month=month, key=key)
        return {"bucket": BUCKET_AUDIT, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{tenant_id}")
def list_audit_archives(tenant_id: str) -> dict[str, Any]:
    resp = s3().list_objects_v2(Bucket=BUCKET_AUDIT, Prefix=f"{tenant_id}/")
    archives = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"tenant_id": tenant_id, "archives": archives}
