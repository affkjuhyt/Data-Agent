"""Contract v1: the wire shapes V2 services, workers and the frontend agree on.

Import from here (`from libs.contracts.v1 import Observation`), never from a
service. The internal domain model and its logic stay in the owning service.
"""

from __future__ import annotations

from enum import StrEnum

from libs.contracts.v1.base import ContractModel, TimeAxes
from libs.contracts.v1.concurrency import VersionedWrite
from libs.contracts.v1.errors import (
    HTTP_STATUS,
    NEXT_ACTION,
    USER_MESSAGE_VI,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    NextAction,
    Refusal,
)
from libs.contracts.v1.events import (
    PARTITION_KEY_FIELD,
    PAYLOAD_MODELS,
    EventEnvelope,
    EventType,
    UnknownPayloadVersion,
    build_envelope,
    parse_envelope,
    payload_model,
    typed_payload,
)
from libs.contracts.v1.ingest import (
    ColumnDiff,
    ColumnProfile,
    DryRunRequest,
    DryRunResult,
    ParseError,
    ParseErrorCode,
    ProfileSummary,
    ReleaseProcessingStatus,
)
from libs.contracts.v1.mapping import MappingRules, TimeRule, ValueRule
from libs.contracts.v1.observation import (
    ChangeKind,
    Observation,
    ObservationCandidate,
    ObsStatus,
    Origin,
    TimeBasis,
    VersionKind,
)
from libs.contracts.v1.payloads import (
    ArtifactStored,
    ExtractionCompleted,
    MappingProposed,
    SourceSlaBreached,
)
from libs.contracts.v1.plan import OutputContract, PlanEdge, PlanIR, PlanNode, Port
from libs.contracts.v1.refs import (
    ApiQueryRef,
    CapabilityRef,
    EntityRef,
    EvidenceRef,
    JsonPathRef,
    PageRef,
    PublicationTimeBasis,
    ReleaseRef,
    ReviewCaseKind,
    ReviewCaseRef,
    SeriesRef,
    TableCellRef,
    XPathRef,
)
from libs.contracts.v1.result_set import (
    ColumnStats,
    GeneratedBy,
    ResultColumn,
    ResultColumnType,
    ResultKind,
    ResultProvenance,
    ResultSet,
    ResultTruncation,
    ResultWarning,
    TrustLabel,
)
from libs.contracts.v1.source_config import (
    Purpose,
    ReleaseTimes,
    SourceConfigV2,
    UseRights,
)
from libs.contracts.v1.verification import VerificationCheck, VerificationFrame

# The models the contract publishes: each appears in generated/contracts.openapi.json
# with its x-schema-version and an example from libs/contracts/examples/v1/.
PUBLIC_MODELS: tuple[type[ContractModel], ...] = (
    EntityRef,
    SeriesRef,
    EvidenceRef,
    ReviewCaseRef,
    CapabilityRef,
    ReleaseRef,
    ObservationCandidate,
    Observation,
    ErrorBody,
    VersionedWrite,
    SourceConfigV2,
    MappingRules,
    ProfileSummary,
    ParseError,
    DryRunRequest,
    DryRunResult,
    EventEnvelope,
    ArtifactStored,
    ExtractionCompleted,
    MappingProposed,
    SourceSlaBreached,
    ResultSet,
    Refusal,
)

# Enums a producer may extend without a version bump (COMPATIBILITY.md): consumers
# must tolerate a value they do not know yet. Every other enum is closed. Published
# as x-open-enum so the compatibility gate reads it from the schema alone.
OPEN_ENUMS: tuple[type[StrEnum], ...] = (ErrorCode, ParseErrorCode, EventType, ReviewCaseKind)

__all__ = [
    "HTTP_STATUS",
    "NEXT_ACTION",
    "OPEN_ENUMS",
    "PARTITION_KEY_FIELD",
    "PAYLOAD_MODELS",
    "PUBLIC_MODELS",
    "USER_MESSAGE_VI",
    "ApiQueryRef",
    "ArtifactStored",
    "CapabilityRef",
    "ChangeKind",
    "ColumnDiff",
    "ColumnProfile",
    "ColumnStats",
    "ContractModel",
    "DryRunRequest",
    "DryRunResult",
    "EntityRef",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "EventEnvelope",
    "EventType",
    "EvidenceRef",
    "ExtractionCompleted",
    "GeneratedBy",
    "JsonPathRef",
    "MappingProposed",
    "MappingRules",
    "NextAction",
    "ObsStatus",
    "Observation",
    "ObservationCandidate",
    "Origin",
    "OutputContract",
    "PageRef",
    "ParseError",
    "ParseErrorCode",
    "PlanEdge",
    "PlanIR",
    "PlanNode",
    "Port",
    "ProfileSummary",
    "PublicationTimeBasis",
    "Purpose",
    "Refusal",
    "ReleaseProcessingStatus",
    "ReleaseRef",
    "ReleaseTimes",
    "ResultColumn",
    "ResultColumnType",
    "ResultKind",
    "ResultProvenance",
    "ResultSet",
    "ResultTruncation",
    "ResultWarning",
    "ReviewCaseKind",
    "ReviewCaseRef",
    "SeriesRef",
    "SourceConfigV2",
    "SourceSlaBreached",
    "TableCellRef",
    "TimeAxes",
    "TimeBasis",
    "TimeRule",
    "TrustLabel",
    "UnknownPayloadVersion",
    "UseRights",
    "ValueRule",
    "VersionKind",
    "VersionedWrite",
    "VerificationCheck",
    "VerificationFrame",
    "XPathRef",
    "build_envelope",
    "parse_envelope",
    "payload_model",
    "typed_payload",
]
