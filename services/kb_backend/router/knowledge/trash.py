"""
/api/knowledge/trash/* — recycle bin for soft-deleted KB entities at three levels:
document (KBData), chunk (KBTextBlock), version (KBTextBlockVersion).

Restore reverses the is_deleted tag in Postgres AND the matching Qdrant points;
flush permanently removes the entity. A document flush removes EVERYTHING keyed by
its data_id: Postgres rows, Qdrant points, Neo4j edges, the MinIO source object, and
the per-document Mongo staging/logs/events.
"""
from fastapi import APIRouter, Depends, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.trash_ops import list_trash, restore, flush

router = APIRouter(prefix="/api/knowledge/trash", tags=["Knowledge-Trash"])


@router.get("/{level}", response_model=ResponseModel)
async def list_trash_route(
    level: str,
    request: Request,
    ctx: dict = Depends(authenticate),
):
    """level ∈ document | chunk | version. Returns the soft-deleted items + total."""
    data = await list_trash(svc(request, "postgres"), level, ctx["tenant_id"])
    return ResponseModel(code=200, data=data)


@router.post("/{level}/{item_id}/restore", response_model=ResponseModel)
async def restore_route(
    level: str,
    item_id: str,
    request: Request,
    ctx: dict = Depends(require_perm("delete_data")),
):
    await restore(svc(request, "postgres"), svc(request, "qdrant"), level, item_id, ctx["tenant_id"])
    return ResponseModel(code=200, data=None)


@router.delete("/{level}/{item_id}", response_model=ResponseModel)
async def flush_route(
    level: str,
    item_id: str,
    request: Request,
    ctx: dict = Depends(require_perm("delete_data")),
):
    """Permanently delete (flush) the item — not reversible."""
    await flush(
        svc(request, "postgres"), svc(request, "qdrant"), svc(request, "neo4j"),
        level, item_id, ctx["tenant_id"],
        minio=svc(request, "minio"), mongo=svc(request, "mongo"),
    )
    return ResponseModel(code=200, data=None)
