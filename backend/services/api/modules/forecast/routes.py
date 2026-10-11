"""A2 group Dự báo: the forecast schema's routes (UC-15-01 to UC-15-08).

Jobs, models and versions, formula forecasts and the forecast ledger each have their own file;
this router gathers them for modules/__init__.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.api.modules.forecast import formula, jobs, ledger, models

router = APIRouter()
router.include_router(jobs.router)
router.include_router(models.router)
router.include_router(formula.router)
router.include_router(ledger.router)
