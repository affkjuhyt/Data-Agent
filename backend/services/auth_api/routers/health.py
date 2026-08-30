from typing import Any

from fastapi import APIRouter

from services.auth_api.auth_core import *
from services.auth_api.schemas import *

router = APIRouter()


@router.get("/api/auth/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# IP ALLOWLIST CONFIG (allow_all toggle)
