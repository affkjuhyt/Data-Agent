"""org module (schema `org`): who the caller is, their roles and Data Spaces.

Public interface: `router` (GET /v1/me, GET /v1/data-spaces) and `OrgIdentityResolver`, which the app installs as
the kernel's identity resolver.
"""

from fastapi import APIRouter

from services.api.modules.org import data_spaces, me
from services.api.modules.org.identity import OrgIdentityResolver

router = APIRouter()
router.include_router(me.router)
router.include_router(data_spaces.router)

__all__ = ["OrgIdentityResolver", "router"]
