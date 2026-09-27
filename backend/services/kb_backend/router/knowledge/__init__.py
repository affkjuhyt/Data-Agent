from fastapi import APIRouter

from services.kb_backend.router.knowledge.data             import router as data_router
from services.kb_backend.router.knowledge.fleet            import router as fleet_router
from services.kb_backend.router.knowledge.light_rag        import router as light_rag_router
from services.kb_backend.router.knowledge.ingestion        import router as ingestion_router
from services.kb_backend.router.knowledge.structured_table import router as structured_table_router
from services.kb_backend.router.knowledge.documents        import router as documents_router
from services.kb_backend.router.knowledge.warehouses       import router as warehouses_router
from services.kb_backend.router.knowledge.warehouse_connect import router as warehouse_connect_router
from services.kb_backend.router.knowledge.sources           import router as sources_router
from services.kb_backend.router.knowledge.conflicts        import router as conflicts_router
from services.kb_backend.router.knowledge.policies         import router as policies_router
from services.kb_backend.router.knowledge.knowledge_base   import router as knowledge_base_router
from services.kb_backend.router.knowledge.qdrant           import router as qdrant_router
from services.kb_backend.router.knowledge.neo4j            import router as neo4j_router
from services.kb_backend.router.knowledge.trash            import router as trash_router

router = APIRouter()
router.include_router(data_router)
router.include_router(fleet_router)
router.include_router(light_rag_router)
router.include_router(ingestion_router)
router.include_router(structured_table_router)
router.include_router(documents_router)
router.include_router(warehouses_router)
router.include_router(warehouse_connect_router)
router.include_router(sources_router)
router.include_router(conflicts_router)
router.include_router(policies_router)
router.include_router(knowledge_base_router)
router.include_router(qdrant_router)
router.include_router(neo4j_router)
router.include_router(trash_router)
