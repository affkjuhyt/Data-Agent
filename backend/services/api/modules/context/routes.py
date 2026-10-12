"""A2 group Lớp ngữ cảnh: the context schema's routes, one file per part of the layer."""

from __future__ import annotations

from fastapi import APIRouter

from services.api.modules.context import routes_knowledge, routes_products, routes_semantic

router = APIRouter()
# /v1/data-products/index before /v1/data-products/{id}, so the literal path wins.
router.include_router(routes_products.router)
router.include_router(routes_semantic.router)
router.include_router(routes_knowledge.router)
