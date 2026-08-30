from typing import Any

from fastapi import APIRouter

from services.release_worker.release_core import *
from services.release_worker.schemas import *

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "release_worker"}
