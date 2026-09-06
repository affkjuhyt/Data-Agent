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


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class APIType(str, Enum):
    NEO4J = "NEO4J"
    QDRANT = "QDRANT"
    RETRIEVE = "RETRIEVE"



class TransactionOp(str, Enum):
    INSERT      = "insert"
    SOFT_DELETE = "soft_delete"
    DELETE      = "delete"


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


class ModelConfig(BaseModel):
    vector_size: Optional[int] = None
    max_tokens: Optional[int] = None
    extra: Optional[dict[str, Any]] = None


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
class KBModelVersionInsert(BaseModel):
    model_id: str
    version_number: int
    added_by: Optional[str] = None
    config: Optional[ModelConfig] = None
    is_active: bool = False


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
    path: Optional[str] = None
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
    embedding_model_id: Optional[str] = None
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
    total_collection: int = 0


@register("insert")
class KBQdrantCollectionInsert(BaseModel):
    connection_id: str
    collection_name: str
    is_active: bool = False
    similarity_metric: SimilarityMetric = SimilarityMetric.COSINE
    points_count: int = 0
    vector_dimension: Optional[int] = None
    embedding_model_id: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KBNeo4jConnectionInsert(TenantModel):
    is_connected: bool = False
    total_node: int = 0
    total_edge: int = 0
    embedding_model_id: Optional[str] = None


@register("insert")
class KBNeo4jNodeInsert(BaseModel):
    connection_id: str
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


@register("insert")
class KBPublishAPIInsert(TenantModel):
    name: str
    type: APIType
    endpoint_url: str
    http_method: HttpMethod
    is_published: bool = False
    model_config = ConfigDict(use_enum_values=True)


"""
Delete Models — PK fields only
"""


@register("delete")
class KBModelDelete(BaseModel):
    model_id: str


@register("delete")
class KBModelVersionDelete(BaseModel):
    version_id: int


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
class KBNeo4jConnectionDelete(TenantModel):
    connection_id: str


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


@register("delete")
class KBPublishAPIDelete(TenantModel):
    id: str


"""
Agent Platform — Insert Models
"""


"""
Auth & Release Enums
"""


class ActorType(str, Enum):
    USER = "USER"
    AGENT = "AGENT"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    SYSTEM = "SYSTEM"


class AuditStatus(str, Enum):
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILED = "FAILED"


class PIIAccessStatus(str, Enum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    REDACTED = "REDACTED"


class KeyType(str, Enum):
    ENCRYPTION_KEY = "ENCRYPTION_KEY"
    SIGNING_KEY = "SIGNING_KEY"
    HMAC_KEY = "HMAC_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"
    MCP_TOKEN = "MCP_TOKEN"
    KB_API_KEY = "KB_API_KEY"


class RotationTrigger(str, Enum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    PANIC = "PANIC"
    REVEAL = "REVEAL"


class RotationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PipelineTriggerType(str, Enum):
    MANUAL = "MANUAL"
    GIT_PUSH = "GIT_PUSH"
    SCHEDULED = "SCHEDULED"


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    BUILDING = "BUILDING"
    SCANNING = "SCANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DEPLOYING = "DEPLOYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PackageStatus(str, Enum):
    BUILDING = "BUILDING"
    SCAN_PENDING = "SCAN_PENDING"
    SCAN_FAILED = "SCAN_FAILED"
    VALIDATED = "VALIDATED"
    PROMOTING = "PROMOTING"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class ReleaseStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class RollbackStatus(str, Enum):
    INITIATED = "INITIATED"
    STEP1_DB = "STEP1_DB"
    STEP2_KONG = "STEP2_KONG"
    STEP3_DEPLOY = "STEP3_DEPLOY"
    SUCCESS = "SUCCESS"
    PARTIAL_ROLLBACK = "PARTIAL_ROLLBACK"
    FAILED = "FAILED"


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


"""
Auth Insert Models
"""


@register("insert")
class LLMProvidersInsert(InsertModel):
    name: str
    model_id: str
    endpoint_url: str
    type: str
    is_default: bool = False
    max_tokens: Optional[int] = None


@register("insert")
class PlanInsert(BaseModel):
    name: str
    max_users: Optional[int] = None
    max_envs: Optional[int] = None
    max_secrets: Optional[int] = None
    max_deploy_daily: Optional[int] = None
    max_api_keys: Optional[int] = None
    features: Optional[dict[str, Any]] = None


@register("insert")
class TenantInsert(BaseModel):
    name: str
    slug: str
    plan_id: Optional[int] = None
    data_residency: str = "Asia-SE1"
    is_active: bool = True


@register("insert")
class KBConnectionsInsert(TenantInsertModel):
    name: str
    endpoint_url: str
    api_key_ref: Optional[str] = None
    status: str = "disconnected"
    created_by: Optional[str] = None


@register("insert")
class RolePermissionInsert(BaseModel):
    role_id: str
    resource: str
    action: str


@register("insert")
class KeycloakRealmConfigInsert(BaseModel):
    tenant_id: str
    realm_name: str
    keycloak_base_url: str
    client_id: str
    client_secret_ref: str
    jwks_url: str
    token_endpoint: str
    token_ttl_seconds: int = 900
    is_active: bool = True


@register("insert")
class IpAllowlistInsert(TenantModel):
    cidr: str
    label: Optional[str] = None
    is_active: bool = True
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
    guardrail_model_id: Optional[str] = None
    created_by: Optional[str] = None


@register("insert")
class ToolsInsert(InsertModel):
    name: str
    description: Optional[str] = None
    type: str
    status: str = "active"
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
    language: str = "vi"
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
    responder_model_id: Optional[str] = None
    llm_config: Optional[dict[str, Any]] = None
    system_prompt_id: Optional[str] = None
    guardrail_id: Optional[str] = None
    memory_enabled: bool = False
    retrieval_config: Optional[dict[str, Any]] = None
    changelog: Optional[str] = None
    created_by: Optional[str] = None


@register("insert")
class AgentKBInsert(InsertModel):
    version_id: str
    kb_connection_id: str


@register("insert")
class AgentToolsInsert(InsertModel):
    version_id: str
    tool_id: str


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
class MemoryPolicyInsert(InsertModel):
    agent_id: str
    action_type: str
    condition: Optional[dict[str, Any]] = None
    enabled: bool = True
    created_by: Optional[str] = None


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


@register("insert")
class ApiKeyInsert(TenantModel):
    created_by: str
    name: str
    key_hash: str
    key_prefix: str
    scope: str = "read_only"
    expires_at: Optional[datetime.datetime] = None
    rotated_from: Optional[str] = None


@register("insert")
class SecretsVaultInsert(TenantModel):
    key_name: str
    key_type: KeyType
    algorithm: Optional[str] = None
    realm: Optional[str] = None
    openbao_path: str
    version: int = 1
    is_active: bool = True
    rotation_due_at: Optional[datetime.datetime] = None
    last_rotated_at: Optional[datetime.datetime] = None
    created_by: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class KeyRotationInsert(BaseModel):
    secret_id: str
    triggered_by: RotationTrigger = RotationTrigger.MANUAL
    actor_id: Optional[str] = None
    old_version: Optional[int] = None
    new_version: Optional[int] = None
    status: RotationStatus = RotationStatus.SUCCESS
    error: Optional[str] = None
    access_reason: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class WebhookInsert(TenantModel):
    created_by: Optional[str] = None
    url: str
    secret_ref: Optional[str] = None
    events: list[str] = []
    is_active: bool = True
    failure_count: int = 0


@register("insert")
class AuditLogInsert(TenantModel):
    actor: str
    actor_type: ActorType = ActorType.USER
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    status: AuditStatus = AuditStatus.SUCCESS
    metadata: Optional[dict[str, Any]] = None
    ip_address: Optional[str] = None
    source_event_id: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class PiiAccessLogInsert(TenantModel):
    accessor_id: Optional[str] = None
    accessor_type: ActorType = ActorType.USER
    data_subject: Optional[str] = None
    field_accessed: Optional[str] = None
    access_reason: Optional[str] = None
    scrubbed: bool = False
    status: PIIAccessStatus = PIIAccessStatus.GRANTED
    model_config = ConfigDict(use_enum_values=True)


"""
Release Insert Models
"""


@register("insert")
class PipelineInsert(BaseModel):
    id: str
    pipeline_name: Optional[str] = None
    triggered_by: str
    trigger_type: PipelineTriggerType = PipelineTriggerType.MANUAL
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    package_version: Optional[str] = None
    target_env: Optional[str] = None
    status: PipelineStatus = PipelineStatus.PENDING
    risk_score: Optional[int] = None
    error_message: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class PipelineStepInsert(BaseModel):
    pipeline_id: str
    step_name: str
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    duration_ms: Optional[int] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class ReleasePackageInsert(BaseModel):
    id: str
    pipeline_id: Optional[str] = None
    artifact_paths: list[str] = []
    validation_score: Optional[int] = None
    status: PackageStatus = PackageStatus.BUILDING
    created_by: str
    environment_targets: list[str] = []
    scan_result: Optional[dict[str, Any]] = None
    promoted_to_s3_at: Optional[datetime.datetime] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class ReleaseApprovalInsert(BaseModel):
    package_id: str
    environment: str
    decision: ApprovalDecision
    approved_by: str
    comment: Optional[str] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class EnvironmentConfigInsert(BaseModel):
    environment: str
    key: str
    value: str
    is_secret: bool = False
    version: int = 1
    is_active: bool = True
    updated_by: Optional[str] = None


@register("insert")
class ReleaseHistoryInsert(BaseModel):
    pipeline_id: str
    package_id: Optional[str] = None
    environment: str
    status: ReleaseStatus
    triggered_by: str
    deployed_at: Optional[datetime.datetime] = None
    duration_ms: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class RollbackOperationInsert(BaseModel):
    id: str
    pipeline_id: Optional[str] = None
    from_version: str
    to_version: str
    environment: str
    triggered_by: str
    reason: Optional[str] = None
    status: RollbackStatus = RollbackStatus.INITIATED
    current_step: int = 0
    steps_result: list[dict[str, Any]] = []
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    alert_sent: bool = False
    model_config = ConfigDict(use_enum_values=True)


@register("insert")
class DriftEventInsert(BaseModel):
    env_pair: str
    drift_keys: list[str] = []
    severity: str = "CRITICAL_DRIFT"
    resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime.datetime] = None
    resolution: Optional[str] = None


"""
Agent Platform — Delete Models
"""


"""
Auth Delete Models
"""


@register("delete")
class LLMProvidersDelete(BaseModel):
    id: str


@register("delete")
class PlanDelete(BaseModel):
    id: int


@register("delete")
class TenantDelete(BaseModel):
    id: str


@register("delete")
class KBConnectionsDelete(TenantModel):
    id: str


@register("delete")
class RolePermissionDelete(BaseModel):
    id: str


@register("delete")
class SystemPromptsDelete(TenantModel):
    id: str


@register("delete")
class KeycloakRealmConfigDelete(BaseModel):
    tenant_id: str


@register("delete")
class IpAllowlistDelete(TenantModel):
    id: str


@register("delete")
class GuardrailsDelete(BaseModel):
    id: str


@register("delete")
class ApiKeyDelete(TenantModel):
    id: str


@register("delete")
class ToolsDelete(BaseModel):
    id: str


@register("delete")
class SecretsVaultDelete(TenantModel):
    id: str


@register("delete")
class MCPDelete(TenantModel):
    id: str


@register("delete")
class KeyRotationDelete(BaseModel):
    id: str


@register("delete")
class AgentsDelete(TenantModel):
    id: str


@register("delete")
class WebhookDelete(TenantModel):
    id: str


@register("delete")
class WorkflowsDelete(BaseModel):
    id: str


@register("delete")
class AuditLogDelete(TenantModel):
    id: str


@register("delete")
class WorkflowVersionsDelete(BaseModel):
    id: str


@register("delete")
class PiiAccessLogDelete(TenantModel):
    id: str


"""
Release Delete Models
"""


@register("delete")
class PipelineDelete(BaseModel):
    id: str


@register("delete")
class AgentVersionsDelete(BaseModel):
    id: str


@register("delete")
class PipelineStepDelete(BaseModel):
    id: int


@register("delete")
class ReleasePackageDelete(BaseModel):
    id: str


@register("delete")
class AgentKBDelete(BaseModel):
    version_id: str
    kb_connection_id: str


@register("delete")
class AgentToolsDelete(BaseModel):
    version_id: str
    tool_id: str


@register("delete")
class AgentMCPDelete(BaseModel):
    version_id: str
    mcp_id: str


@register("delete")
class AgentMemoriesDelete(BaseModel):
    id: str


@register("delete")
class ReleaseApprovalDelete(BaseModel):
    id: int


@register("delete")
class EnvironmentConfigDelete(BaseModel):
    id: int


@register("delete")
class ReleaseHistoryDelete(BaseModel):
    id: int


@register("delete")
class RollbackOperationDelete(BaseModel):
    id: str


@register("delete")
class MemoryPolicyDelete(BaseModel):
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


@register("delete")
class DriftEventDelete(BaseModel):
    id: int


"""
Transaction
"""


class TransactionJobResult(BaseModel):
    method: TransactionOp
    table: str
    success: bool


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
