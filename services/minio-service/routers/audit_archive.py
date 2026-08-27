from typing import Any

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from auth import get_current_user
from clients.s3 import s3
from config import BUCKET_AUDIT

log = structlog.get_logger()
router = APIRouter(prefix="/api/storage/audit-archive")

# A tenant id in the PATH is a request, not a fact. Authentication proves who is
# calling; this is what stops an authenticated user of one tenant from naming
# another tenant's id in the URL and reading — or overwriting — its audit
# archive. Platform-admin is exempt because cross-tenant archive access is the
# operator role's actual job.
_PLATFORM_ADMIN = "platform-admin"


def _authorize_tenant(tenant_id: str, caller: dict[str, Any]) -> None:
    if _PLATFORM_ADMIN in (caller.get("roles") or []):
        return
    if (caller.get("tenant_id") or "") != tenant_id:
        log.warning(
            "audit.archive.denied", requested_tenant=tenant_id,
            caller_tenant=caller.get("tenant_id"), user=caller.get("user_id"),
        )
        raise HTTPException(status_code=403, detail="Not permitted for this tenant")



@router.put("/{tenant_id}/{year}/{month}", status_code=201)
def upload_audit_archive(
    tenant_id: str, year: int, month: int, file: UploadFile = File(...),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _authorize_tenant(tenant_id, caller)
    key = f"{tenant_id}/{year}/{month:02d}.jsonl"
    try:
        s3().upload_fileobj(file.file, BUCKET_AUDIT, key)
        log.info("audit.archive.uploaded", tenant_id=tenant_id, year=year, month=month, key=key)
        return {"bucket": BUCKET_AUDIT, "key": key}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{tenant_id}")
def list_audit_archives(
    tenant_id: str, caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _authorize_tenant(tenant_id, caller)
    resp = s3().list_objects_v2(Bucket=BUCKET_AUDIT, Prefix=f"{tenant_id}/")
    archives = [
        {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
        for obj in resp.get("Contents", [])
    ]
    return {"tenant_id": tenant_id, "archives": archives}
