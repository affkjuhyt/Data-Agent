"""KB API schemas (Pydantic request/response/domain), organized by domain.
Flat re-export: `from services.kb_backend.schemas import DocResponse, ...`."""
from services.kb_backend.schemas.enums import *  # noqa: F401,F403
from services.kb_backend.schemas.common import (  # noqa: F401
    to_string, parse_jsonb, coerce_jsonb_dict,
    StatusResponse, ConfigActivateResponse, VersionCreatedResponse,
    ConfigCreatedResponse, PolicyCreatedResponse, RowUpdateResponse,
)
from services.kb_backend.schemas.fleet import FleetContent, FleetStats  # noqa: F401
from services.kb_backend.schemas.document import (  # noqa: F401
    Document, Image, Video, Web, MetadataType, DocumentMetadata,
    UpdateDocumentRequest, DocResponse,
)
from services.kb_backend.schemas.chunk import (  # noqa: F401
    ChunkVersionDetail, ChunkVersion, ActivateChunkVersionRequest,
    CreateChunkVersionRequest, CreateDocConfigRequest, ChunkResponse,
)
from services.kb_backend.schemas.table import (  # noqa: F401
    TableColumn, UpdateTableRowRequest, TableResponse,
)
from services.kb_backend.schemas.conflict import (  # noqa: F401
    ConflictSummary, PendingConflictBatch,
    ConflictBucketsResponse, ConflictDetailResponse, ConflictResolvedResponse,
    BatchConflictChoice, ResolveBatchRequest, BatchResolvedResponse,
)
from services.kb_backend.schemas.policy import (  # noqa: F401
    CreateFilterPolicyRequest, UpdateFilterPolicyRequest, UpdateExtractionPolicyRequest,
    FilterPolicyResponse, ExtractionPolicyResponse,
)
from services.kb_backend.schemas.warehouse import (  # noqa: F401
    KnowledgeConfigTable, CreateWarehouseConfigRequest, ConnectSnowflakeRequest,
    SelectTablesRequest, WarehouseConfigResponse,
)
from services.kb_backend.schemas.sources import (  # noqa: F401
    CreateSourceRequest, UpdateSourceConfigRequest, SourceResponse,
    SourceRunResponse, BulkImportRequest, BulkImportSkipped, BulkImportResponse,
)
from services.kb_backend.schemas.qdrant import (  # noqa: F401
    QdrantSearchRequest, ToggleQdrantCollectionRequest,
    QdrantCollectionResponse, QdrantPointResponse, ToggleCollectionResponse,
)
from services.kb_backend.schemas.neo4j import (  # noqa: F401
    Neo4jNode, Neo4jEdge, Neo4jQueryRequest, Neo4jGraphResponse, Neo4jSchemaResponse,
)
from services.kb_backend.schemas.knowledge_base import CreateKnowledgeBaseRequest  # noqa: F401
from services.kb_backend.schemas.ingestion import WebIngestRequest, PromoteResponse  # noqa: F401
from services.kb_backend.schemas.retrieval import RetrieveRequest, RetrieveResponse  # noqa: F401
