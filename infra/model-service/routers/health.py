from fastapi import APIRouter
from models import is_ready

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok" if is_ready() else "loading"}
