from typing import Any

from fastapi import APIRouter

from release_core import *
from schemas import *

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "release-worker"}
