import datetime
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Annotated, Callable, Literal, Optional, Self, Type, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ROLES = frozenset({"insert", "delete", "orm"})
_SUFFIXES = ("ORM", "Insert", "Delete")
_partial: dict[str, dict[str, Any]] = {}

Method = Literal["insert", "delete", "orm"]


@dataclass
class OperationSchema:
    insert: Type[BaseModel]
    delete: Type[BaseModel]
    orm: Any


REGISTRY: dict[str, OperationSchema] = {}
MODEL_TO_TABLE: dict[type, str] = {}


def register(method: Method) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        table = cls.__name__
        for suffix in _SUFFIXES:
            if table.endswith(suffix):
                table = table[:-len(suffix)]
                break
        if table not in _partial:
            _partial[table] = {}
        _partial[table][method] = cls
        MODEL_TO_TABLE[cls] = table
        if _ROLES.issubset(_partial[table]):
            REGISTRY[table] = OperationSchema(**{r: _partial[table][r] for r in _ROLES})
        return cls
    return decorator


"""
Enum
"""


class Language(str, Enum):
    EN = "english"
    VN = "vietnamese"

class SourceType(str, Enum):
    DOC = "doc"
    WEB = "web"
    IMAGE = "image"
    VIDEO = "video"
    WAREHOUSE = "warehouse"


class Tier(str, Enum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class ActiveStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class PolicyFilteringType(str, Enum):
    NATURAL_LANG = "natural_language"
    EXACT_MATCH = "exact_word"


class PolicyExtractionType(str, Enum):
    ENTITY_NODE = "entity_node"
    RELATIONSHIP_EDGE = "relationship_edge"


class ConflictType(str, Enum):
    CONTENT_CONTRADICTION = "content_contradiction"
    CONTENT_CONFLICT = "content_conflict"
    CONTENT_DUPLICATE = "content_duplicate"
    CONTENT_UPDATE = "content_update"
    TABLE_SCHEMA = "table_schema"


class ConflictResolution(str, Enum):
    KEEP_EXISTING = "keep_existing"
    KEEP_INCOMING = "keep_incoming"
    MERGE = "merge"
    DELETE = "delete"
    NO_ACTION = "no_action"


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConflictStatus(str, Enum):
    PENDING = "pending"
    AWAITING = "awaiting"
    RESOLVED = "resolved"


class TaskType(str, Enum):
    EMBEDDING = "embedding"
    VLM = "vision_language_model"


class SimilarityMetric(str, Enum):
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    DOT = "dot"


class FilterOperator(str, Enum):
    EQ  = "eq"
    NE  = "ne"
    GT  = "gt"
    LT  = "lt"
    GTE = "gte"
    LTE = "lte"
    IN  = "in"   # value is a list — SQL `column IN (...)`. Multi-select filtering.


class OrderDirection(str, Enum):
    ASC  = "ASC"
    DESC = "DESC"


"""
Base Struct
"""


class DMLPreparation(BaseModel):
    instance: Any | None = None
    orm_cls: Any | None = None
    table_name: str | None = None
    compiled_query: Any | None = None


class DQLPreparation(BaseModel):
    compiled_query: Any


class Document(BaseModel):
    source_type: Literal[SourceType.DOC]
    doc_type: str
    author: str | None = None
    published_date: datetime.datetime | None = None
    file_size: int | None = None


class Image(BaseModel):
    source_type: Literal[SourceType.IMAGE]
    image_type: str
    height: int
    width: int
    color_space: str | None = None
    file_size: int | None = None


class Video(BaseModel):
    source_type: Literal[SourceType.VIDEO]
    video_type: str
    height: int
    width: int
    codec: str | None = None
    total_frame: int
    file_size: int | None = None


class Web(BaseModel):
    source_type: Literal[SourceType.WEB]
    url: str
    web_name: str


class Warehouse(BaseModel):
    source_type: Literal[SourceType.WAREHOUSE]
    warehouse_type: str | None = None


MetadataType = Annotated[
    Union[Document, Image, Video, Web, Warehouse],
    Field(discriminator="source_type"),
]


class PolicyConfig(BaseModel):
    rules: Optional[list[str]]
    threshold: Optional[float] = None
    extra: Optional[dict[str, Any]] = None


class WarehouseConfigPayload(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    selected_tables: Optional[list[Any]] = None  # list of table objects {name, schema, rowCount, description, columns}
    sync_schedule: Optional[str] = None
    schema_filter: Optional[list[str]] = None
    connection: Optional[dict[str, Any]] = None  # full connection credentials/details
    extra: Optional[dict[str, Any]] = None


class SourceConnectorType(str, Enum):
    """V1 connectors (http_file/web_crawl/web_crawl_headless) land in the same
    Bronze pipeline a manual Document Hub / Web Portal upload does. V2 adds
    `rss` (same fit — a feed is just another document-ish artifact) and,
    as an accepted interim tradeoff, `rest_api`/`sdmx`: their eventual home is
    the not-yet-built Indicator Service warehouse, but until that exists they
    land raw response bytes in Bronze the same way `http_file` does, rather
    than blocking on that schema. `bulk_parquet` stays out of this enum on
    purpose — it's a one-off dataset import (see `model/eval`), never a
    scheduled `KBSource`."""
    HTTP_FILE = "http_file"
    WEB_CRAWL = "web_crawl"
    WEB_CRAWL_HEADLESS = "web_crawl_headless"
    RSS = "rss"
    REST_API = "rest_api"
    SDMX = "sdmx"


class SourceConfigPayload(BaseModel):
    """`KBSourceConfig.config` JSONB shape. `render` only matters for
    `web_crawl`/`web_crawl_headless` (Playwright vs static httpx); ignored by
    `http_file`/`rest_api`/`sdmx`/`rss`. `openbao_path`, when set, is a vault
    path (KV v2, e.g. `tenants/<tenant_id>/<key>`) resolved at run time by the
    web-crawler worker — never a raw secret, the same `tenants/<tenant>/<key>`
    layout auth_api's Secrets Vault writes. `query_params`/`auth_style`/
    `auth_param_name` only apply to `rest_api`/`sdmx`. `recursive`/
    `max_depth`/`same_domain_only` only apply to `web_crawl`/
    `web_crawl_headless` — link-discovery crawl, see `KBSourceCrawlQueue`."""
    url: str
    format: Optional[str] = None
    cron: str
    render: bool = False
    variables: Optional[list[str]] = None
    openbao_path: Optional[str] = None
    query_params: Optional[dict[str, str]] = None
    auth_style: Optional[str] = None  # "bearer" (default when openbao_path is set) | "query_param"
    auth_param_name: Optional[str] = None  # required when auth_style == "query_param" (e.g. "api_key")
    recursive: bool = False
    max_depth: int = 1
    same_domain_only: bool = True
    # "auto": the worker itself queues Silver then Gold after a successful,
    # changed crawl — a scheduled source has no one to click the button.
    # "manual": promotion stays a human action, as it always has been.
    # create_source defaults this to "auto" for any source with a cron,
    # "manual" otherwise (see source_ops.py).
    promotion_mode: Literal["manual", "auto"] = "manual"
    # Stamped onto every KBData row this source's runs produce (see
    # KBDataInsert.purpose) — a scheduled source keeps tagging the same
    # purpose on each re-crawl without a steward re-choosing it every time.
    purpose: Literal["training", "production", "evaluation"] = "production"
    notes: Optional[str] = None


class SourceRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class SourceRunTrigger(str, Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    # A link discovered by a recursive web_crawl source's own earlier run,
    # dispatched from KBSourceCrawlQueue rather than the source's own cron.
    RECURSIVE = "recursive"


"""
Base Models
"""

class InsertModel(BaseModel):
    inserted_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class TenantInsertModel(InsertModel):
    tenant_id: str


class TenantModel(BaseModel):
    tenant_id: str


"""
Insert Models
tables with tenant_id extend TenantInsertModel; tables without extend InsertModel
"""

@register("insert")
class KBModelInsert(BaseModel):
    model_name: str
    task_type: TaskType
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBDataInsert(TenantModel):
    role_id: str
    knowledge_base_id: Optional[str] = None
    name: str
    extension: str
    language: Language
    source_type: SourceType
    added_by: str
    abstract: str
    doc_metadata: MetadataType
    current_tier: Tier = Tier.BRONZE
    ingestion_mode: str = "document"
    purpose: str = "production"
    path: Optional[str] = None
    content_hash: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBKnowledgeBaseInsert(TenantModel):
    name: str
    description: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None


@register("delete")
class KBKnowledgeBaseDelete(TenantModel):
    knowledge_base_id: str


@register("insert")
class KBLifecycleHistoryInsert(BaseModel):
    data_id: str
    to_tier: Tier
    from_tier: Optional[Tier] = None
    approved_by: Optional[str] = None
    notes: Optional[str] = None
    pipeline_metadata: Optional[dict[str, Any]] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBFilterPolicyInsert(TenantModel):
    policy_name: str
    configformat: PolicyFilteringType
    config: Optional[PolicyConfig] = None
    is_active: bool = False
    created_by: Optional[str] = None
    language: Optional[Language] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBExtractionPolicyInsert(TenantModel):
    policy_name: str
    policy_type: PolicyExtractionType
    custom_override: Optional[str] = None
    created_by: Optional[str] = None
    language: Optional[Language] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBConflictBatchInsert(TenantModel):
    batch_title: str
    existing_block_id: Optional[str] = None
    status: ConflictStatus = ConflictStatus.PENDING
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBConflictInsert(TenantModel):
    """The conflict's index row. Its chunk payload lives in Mongo, keyed (tenant_id, conflict_id)."""
    conflict_type: ConflictType
    severity: ConflictSeverity
    data_id: Optional[str] = None
    batch_id: Optional[str] = None
    status: ConflictStatus = ConflictStatus.PENDING
    detailed_explanation: Optional[str] = None
    resolution_instruction: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBWarehouseInsert(BaseModel):
    tenant_id: str
    service: str
    description: Optional[str] = None


@register("insert")
class KBWarehouseConfigInsert(BaseModel):
    warehouse_id: str
    version_number: int
    is_active: bool = False
    config: Optional[WarehouseConfigPayload] = None
    created_by: Optional[str] = None


@register("insert")
class KBSourceInsert(BaseModel):
    tenant_id: str
    role_id: str
    connector_type: SourceConnectorType
    title: str
    publisher: Optional[str] = None
    created_by: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBSourceConfigInsert(BaseModel):
    source_id: str
    version_number: int
    is_active: bool = False
    config: Optional[SourceConfigPayload] = None
    created_by: Optional[str] = None


@register("insert")
class KBSourceRunInsert(BaseModel):
    source_id: str
    status: SourceRunStatus = SourceRunStatus.RUNNING
    trigger: SourceRunTrigger = SourceRunTrigger.SCHEDULED
    data_ids: Optional[list[str]] = None
    error: Optional[str] = None
    quality_metrics: Optional[dict[str, Any]] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBTableInsert(BaseModel):
    owner_id: str
    table_name: str
    description: Optional[str] = None
    table_schema: Optional[dict[str, Any]] = None
    created_by: Optional[str] = None


@register("insert")
class KBStructuredImportJobInsert(BaseModel):
    owner_id: str
    status: str = "proposing"
    llm_proposed_schema: Optional[dict[str, Any]] = None
    human_edited_schema: Optional[dict[str, Any]] = None
    sample_rows: Optional[dict[str, Any]] = None
    created_table_name: Optional[str] = None
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime.datetime] = None


@register("insert")
class KBTextBlockInsert(BaseModel):
    owner_id: str
    block_index: int


@register("insert")
class KBTextBlockVersionInsert(BaseModel):
    block_id: str
    version_number: int
    content: Optional[str] = None
    created_by: Optional[str] = None
    table_involved: Optional[bool] = None
    payload: Optional[dict[str, Any]] = None
    is_active: bool = False


@register("insert")
class KBTextTableInsert(BaseModel):
    version_id: str
    table_name: str
    description: Optional[str] = None
    data: Optional[dict[str, Any]] = None


@register("insert")
class KBQdrantConnectionInsert(TenantModel):
    is_active: bool = False


@register("insert")
class KBQdrantCollectionInsert(BaseModel):
    connection_id: str
    collection_name: str
    is_active: bool = False
    embedding_model_id: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBNeo4jNodeInsert(BaseModel):
    node_name: str
    node_description: Optional[str] = None


@register("insert")
class KBNeo4jRelationshipInsert(BaseModel):
    from_node: str
    to_node: str
    score: Optional[float] = None
    description: Optional[str] = None


@register("insert")
class KBEntityLookupInsert(BaseModel):
    alias_name: str
    canonical_name: str
    # NULL = global seed alias shared by all tenants
    tenant_id: Optional[uuid.UUID] = None


"""
Delete Models — PK fields only
"""


@register("delete")
class KBModelDelete(BaseModel):
    model_id: str


@register("delete")
class KBDataDelete(TenantModel):
    data_id: str


@register("delete")
class KBLifecycleHistoryDelete(BaseModel):
    history_id: str


@register("delete")
class KBFilterPolicyDelete(TenantModel):
    policy_id: str


@register("delete")
class KBExtractionPolicyDelete(TenantModel):
    policy_id: str


@register("delete")
class KBConflictBatchDelete(TenantModel):
    batch_id: str


@register("delete")
class KBConflictDelete(TenantModel):
    conflict_id: str


@register("delete")
class KBWarehouseDelete(BaseModel):
    warehouse_id: str


@register("delete")
class KBWarehouseConfigDelete(BaseModel):
    config_id: str


@register("delete")
class KBSourceDelete(BaseModel):
    source_id: str


@register("delete")
class KBSourceConfigDelete(BaseModel):
    config_id: str


@register("delete")
class KBTableDelete(BaseModel):
    table_id: str


@register("delete")
class KBStructuredImportJobDelete(BaseModel):
    job_id: str


@register("delete")
class KBTextBlockDelete(BaseModel):
    block_id: str


@register("delete")
class KBTextBlockVersionDelete(BaseModel):
    version_id: str


@register("delete")
class KBTextTableDelete(BaseModel):
    version_id: str


@register("delete")
class KBQdrantConnectionDelete(TenantModel):
    connection_id: str


@register("delete")
class KBQdrantCollectionDelete(BaseModel):
    collection_id: str


@register("delete")
class KBNeo4jNodeDelete(BaseModel):
    node_id: str


@register("delete")
class KBNeo4jRelationshipDelete(BaseModel):
    from_node: str
    to_node: str


@register("delete")
class KBEntityLookupDelete(BaseModel):
    lookup_id: str


"""
Auth Insert Models
"""


@register("insert")
class KBConnectionsInsert(TenantInsertModel):
    name: str
    endpoint_url: str
    api_key_ref: Optional[str] = None
    status: str = "disconnected"
    created_by: Optional[str] = None


@register("insert")
class SystemPromptsInsert(TenantInsertModel):
    name: str
    content: str
    version: int = 1
    is_active: bool = True
    created_by: Optional[str] = None


@register("insert")
class GuardrailsInsert(InsertModel):
    name: str
    type: str
    conditions: dict[str, Any] = {}
    action: str
    priority: int = 0
    guardrail_model: Optional[str] = None
    created_by: Optional[str] = None


@register("insert")
class MCPInsert(TenantInsertModel):
    name: str
    endpoint_url: str
    api_key_ref: Optional[str] = None
    status: str = "disconnected"
    capabilities: Optional[dict[str, Any]] = None
    created_by: Optional[str] = None


@register("insert")
class AgentsInsert(TenantInsertModel):
    name: str
    description: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None


@register("insert")
class WorkflowsInsert(InsertModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None


@register("insert")
class WorkflowVersionsInsert(InsertModel):
    workflow_id: str
    version: int
    status: str = "draft"
    changelog: Optional[str] = None
    created_by: Optional[str] = None


@register("insert")
class AgentVersionsInsert(InsertModel):
    agent_id: str
    version: int
    status: str = "draft"
    workflow_version_id: Optional[str] = None
    responder_model: Optional[str] = None
    llm_config: Optional[dict[str, Any]] = None
    system_prompt_id: Optional[str] = None
    memory_enabled: bool = False
    changelog: Optional[str] = None
    created_by: Optional[str] = None


@register("insert")
class AgentKBInsert(InsertModel):
    version_id: str
    kb_connection_id: str


@register("insert")
class AgentMCPInsert(InsertModel):
    version_id: str
    mcp_id: str


@register("insert")
class AgentMemoriesInsert(TenantInsertModel):
    agent_id: str
    scope: str = "global"
    user_ref: Optional[str] = None
    memory_type: str
    content: str
    embedding_ref: Optional[str] = None
    importance: Optional[float] = 0.5
    expires_at: Optional[datetime.datetime] = None


@register("insert")
class ConversationsInsert(TenantInsertModel):
    user_ref: Optional[str] = None
    channel: str = "web"
    status: str = "active"


@register("insert")
class MessagesInsert(InsertModel):
    conversation_id: str
    role: str
    content: dict[str, Any]
    msg_metadata: Optional[dict[str, Any]] = None
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None


@register("insert")
class AgentTracesInsert(InsertModel):
    message_id: str
    trace_index: int
    tool_name: str
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    status: str = "success"
    latency_ms: Optional[int] = None


"""
Auth Delete Models
"""


@register("delete")
class KBConnectionsDelete(TenantModel):
    id: str


@register("delete")
class SystemPromptsDelete(TenantModel):
    id: str


@register("delete")
class GuardrailsDelete(BaseModel):
    id: str


@register("delete")
class MCPDelete(TenantModel):
    id: str


@register("delete")
class AgentsDelete(TenantModel):
    id: str


@register("delete")
class WorkflowsDelete(BaseModel):
    id: str


@register("delete")
class WorkflowVersionsDelete(BaseModel):
    id: str


"""
Release Delete Models
"""


@register("delete")
class AgentVersionsDelete(BaseModel):
    id: str


@register("delete")
class AgentKBDelete(BaseModel):
    version_id: str
    kb_connection_id: str


@register("delete")
class AgentMCPDelete(BaseModel):
    version_id: str
    mcp_id: str


@register("delete")
class AgentMemoriesDelete(BaseModel):
    id: str


@register("delete")
class ConversationsDelete(BaseModel):
    id: str


@register("delete")
class MessagesDelete(BaseModel):
    id: str


@register("delete")
class AgentTracesDelete(BaseModel):
    id: str


"""
Read — generic join request (also handles single-table reads)
"""


class SelectedColumn(BaseModel):
    table_name: str
    column_name: str
    alias: Optional[str] = None


class WhereFilter(BaseModel):
    table_name: str
    column_name: str
    value: Any
    operator: FilterOperator = FilterOperator.EQ


class OrderBy(BaseModel):
    table_name: str
    column: str
    order: OrderDirection = OrderDirection.ASC


class SelectInLoadRequest(BaseModel):
    tenant_id: Optional[str] = None
    table: str
    load_paths: list[str]               # table name dot-notation e.g. ["KBTextBlock.KBTextBlockVersion", "KBTable"]
    filters: list[WhereFilter] = []
    limit: int = 50
    cursor: Optional[dict[str, Any]] = None  # last row of prior page (keyset); see pagination.advance
    order_by: Optional[OrderBy] = None


class ReadJoinRequest(BaseModel):
    tenant_id: Optional[str] = None
    joins_table: list[str]
    selected_columns: list[SelectedColumn] = []
    filters: list[WhereFilter] = []
    limit: int = 50
    cursor: Optional[dict[str, Any]] = None  # last row of prior page (keyset); see pagination.advance
    order_by: Optional[OrderBy] = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if not self.joins_table:
            raise ValueError("joins_table must contain at least one table name")
        table_set = set(self.joins_table)
        for col in self.selected_columns:
            if col.table_name not in table_set:
                raise ValueError(f"selected_columns references unknown table '{col.table_name}'")
        for f in self.filters:
            if f.table_name not in table_set:
                raise ValueError(f"filters references unknown table '{f.table_name}'")
        return self
