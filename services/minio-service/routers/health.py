from typing import Any

from fastapi import APIRouter, HTTPException

from clients.s3 import s3

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    try:
        s3().list_buckets()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
