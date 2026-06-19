from fastapi import APIRouter
from parser import is_ready

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok" if is_ready() else "loading"}
