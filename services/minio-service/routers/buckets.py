from typing import Any

import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException

from clients.s3 import s3
from schemas import BucketCreate

log = structlog.get_logger()
router = APIRouter(prefix="/api/storage/buckets")


@router.get("")
def list_buckets() -> dict[str, Any]:
    resp = s3().list_buckets()
    return {"buckets": [b["Name"] for b in resp.get("Buckets", [])]}


@router.post("", status_code=201)
def create_bucket(body: BucketCreate) -> dict[str, Any]:
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
