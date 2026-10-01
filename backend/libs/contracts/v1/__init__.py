"""Contract v1: the wire shapes V2 services, workers and the frontend agree on.

Import from here (`from libs.contracts.v1 import Observation`), never from a
service. The internal domain model and its logic stay in the owning service.
"""

from __future__ import annotations

from libs.contracts.v1.base import ContractModel, TimeAxes
from libs.contracts.v1.concurrency import VersionedWrite
from libs.contracts.v1.errors import HTTP_STATUS, ErrorBody, ErrorCode, ErrorDetail
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
from libs.contracts.v1.source_config import (
    Purpose,
    ReleaseTimes,
    SourceConfigV2,
    UseRights,
)

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
)

__all__ = [
    "HTTP_STATUS",
    "PARTITION_KEY_FIELD",
    "PAYLOAD_MODELS",
    "PUBLIC_MODELS",
    "ApiQueryRef",
    "ArtifactStored",
    "CapabilityRef",
    "ChangeKind",
    "ColumnDiff",
    "ColumnProfile",
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
    "JsonPathRef",
    "MappingProposed",
    "MappingRules",
    "ObsStatus",
    "Observation",
    "ObservationCandidate",
    "Origin",
    "PageRef",
    "ParseError",
    "ParseErrorCode",
    "ProfileSummary",
    "PublicationTimeBasis",
    "Purpose",
    "ReleaseProcessingStatus",
    "ReleaseRef",
    "ReleaseTimes",
    "ReviewCaseKind",
    "ReviewCaseRef",
    "SeriesRef",
    "SourceConfigV2",
    "SourceSlaBreached",
    "TableCellRef",
    "TimeAxes",
    "TimeBasis",
    "TimeRule",
    "UnknownPayloadVersion",
    "UseRights",
    "ValueRule",
    "VersionKind",
    "VersionedWrite",
    "XPathRef",
    "build_envelope",
    "parse_envelope",
    "payload_model",
    "typed_payload",
]
