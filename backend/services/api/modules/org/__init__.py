"""org module (schema `org`): who the caller is, their roles and Data Spaces.

Public interface: `router` (GET /v1/me, GET /v1/data-spaces, Quản trị), `OrgIdentityResolver`, which
the app installs as the kernel's identity resolver, and `capabilities_of` (another person's
capabilities in a Data Space, for MCP clients acting for their owner).
"""

from fastapi import APIRouter

from services.api.modules.org import admin, data_spaces, me
from services.api.modules.org.identity import OrgIdentityResolver
from services.api.modules.org.public import capabilities_of

router = APIRouter()
router.include_router(me.router)
router.include_router(data_spaces.router)
router.include_router(admin.router)

__all__ = ["OrgIdentityResolver", "capabilities_of", "router"]
