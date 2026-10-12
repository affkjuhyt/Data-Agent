"""Quản trị (A2) over org.*: users and roles, group mappings, emergency access, Data Spaces and
their members and sources, access requests, work calendars, and the principal picker with the role
and member lists (directory.py).

The DSR endpoints of the group (/v1/dsr*) write governance.dsr_request (tab 3b D4.2) and so belong
to the governance module, not here.
"""

from fastapi import APIRouter

from services.api.modules.org.admin import access, calendars, directory, spaces, users

router = APIRouter()
router.include_router(users.router)
router.include_router(spaces.router)
router.include_router(access.router)
router.include_router(calendars.router)
router.include_router(directory.router)

__all__ = ["router"]
