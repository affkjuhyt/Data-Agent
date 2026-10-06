"""The envelope every V2 Kafka event travels in (envelope v1, 03 section 13).

The envelope is versioned on its own (`envelope_version`), and each payload has
its own `schema_version` — neither follows the storage schema: a column added to
`ingest.release` changes no event until a payload model here changes.

Producers write the envelope through the existing outbox:
`outbox.enqueue(session, producer=..., topic=env.event_type, key=env.partition_key,
value=env.model_dump(mode="json"))`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, ValidationError, model_validator

from libs.contracts.v1.base import ContractModel, NonEmptyStr
from libs.contracts.v1.payloads import (
    ArtifactStored,
    ExtractionCompleted,
    MappingProposed,
    SourceSlaBreached,
)


class EventType(StrEnum):
    """The POC topics of 03 section 13 (forecast.* comes after the POC)."""

    ARTIFACT_STORED = "artifact.stored"
    SOURCE_SLA_BREACHED = "source.sla.breached"
    EXTRACTION_COMPLETED = "extraction.completed"
    MAPPING_PROPOSED = "mapping.proposed"
    VALIDATION_COMPLETED = "validation.completed"
    REVIEW_CASE_CREATED = "review.case.created"
    REVIEW_APPROVED = "review.approved"
    OBSERVATION_PUBLISHED = "observation.published"
    INDICATOR_RECOMPUTED = "indicator.recomputed"
    KNOWLEDGE_PROPOSAL_CREATED = "knowledge.proposal.created"
    KNOWLEDGE_VERIFIED = "knowledge.verified"
    ALERT_RAISED = "alert.raised"


# The payload field each topic is keyed by — the "Khoá" column of 03 section 13.
# Kafka orders events only within a partition: two events of one series_key on
# different partitions can be applied out of order, and a recompute then
# overwrites a newer value with an older one.
PARTITION_KEY_FIELD: dict[EventType, str] = {
    EventType.ARTIFACT_STORED: "source_id",
    EventType.SOURCE_SLA_BREACHED: "source_id",
    EventType.MAPPING_PROPOSED: "source_id",
    EventType.EXTRACTION_COMPLETED: "release_id",
    EventType.VALIDATION_COMPLETED: "release_id",
    EventType.REVIEW_CASE_CREATED: "case_id",
    EventType.REVIEW_APPROVED: "case_id",
    EventType.OBSERVATION_PUBLISHED: "series_key",
    EventType.INDICATOR_RECOMPUTED: "series_key",
    EventType.KNOWLEDGE_PROPOSAL_CREATED: "object_id",
    EventType.KNOWLEDGE_VERIFIED: "object_id",
    EventType.ALERT_RAISED: "entity_id",
}

# (topic, payload schema_version) → payload model. A topic listed here refuses
# any payload that does not validate, and any schema_version it does not list:
# a consumer must not guess the shape of a version it was not built for — the
# message goes to the topic's DLQ instead.
PAYLOAD_MODELS: dict[tuple[EventType, int], type[ContractModel]] = {
    (EventType.ARTIFACT_STORED, 1): ArtifactStored,
    (EventType.EXTRACTION_COMPLETED, 1): ExtractionCompleted,
    (EventType.MAPPING_PROPOSED, 1): MappingProposed,
    (EventType.SOURCE_SLA_BREACHED, 1): SourceSlaBreached,
}
_TYPED_TOPICS = {event_type for event_type, _ in PAYLOAD_MODELS}


class UnknownPayloadVersion(ValueError):
    """A typed topic arrived with a schema_version this contract does not define."""


class EventEnvelope(ContractModel):
    # Merged with ContractModel.model_config by pydantic.
    model_config = ConfigDict(frozen=True)

    envelope_version: Literal[1] = 1
    event_id: UUID
    event_type: EventType
    schema_version: int = Field(ge=1, description="Version of this event type's payload")
    tenant_id: UUID
    occurred_at: AwareDatetime
    producer: NonEmptyStr = Field(description="web_crawler, series_extractor, …")
    trace_id: str | None = None
    idempotency_key: NonEmptyStr = Field(
        description="Consumers apply an event once per key; equals event_id unless the producer "
        "derives it from the business operation"
    )
    partition_key: NonEmptyStr = Field(description="Kafka key, i.e. the ordering key")
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches(self) -> Self:
        # A key that disagrees with the payload would route the event to the
        # wrong partition while looking valid, so it is refused outright.
        expected = _key_from_payload(self.event_type, self.payload)
        if self.partition_key != expected:
            field = PARTITION_KEY_FIELD[self.event_type]
            raise ValueError(
                f"partition_key {self.partition_key!r} does not match payload.{field} {expected!r}"
            )
        if self.event_type in _TYPED_TOPICS:
            model = payload_model(self.event_type, self.schema_version)
            try:
                model.model_validate(self.payload)
            except ValidationError as exc:
                # Re-raised as a plain error so it is reported against the
                # envelope; a bare nested ValidationError would surface with the
                # payload's field names as if they were envelope fields.
                problems = "; ".join(
                    f"payload.{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
                )
                raise ValueError(f"payload is not a valid {model.__name__}: {problems}") from None
        return self


def payload_model(event_type: EventType, schema_version: int) -> type[ContractModel]:
    try:
        return PAYLOAD_MODELS[(event_type, schema_version)]
    except KeyError:
        raise UnknownPayloadVersion(
            f"{event_type.value} has no payload schema_version {schema_version}"
        ) from None


def _key_from_payload(event_type: EventType, payload: dict[str, Any]) -> str:
    field = PARTITION_KEY_FIELD[event_type]
    value = payload.get(field)
    if value is None or str(value) == "":
        raise ValueError(f"{event_type.value} payload is missing its partition key field {field!r}")
    return str(value)


def build_envelope(
    event_type: EventType,
    payload: ContractModel | dict[str, Any],
    *,
    tenant_id: UUID,
    producer: str,
    schema_version: int | None = None,
    trace_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    """Wrap a payload. The partition key is always taken from the payload, never passed in."""
    if isinstance(payload, ContractModel):
        version = schema_version or type(payload).SCHEMA_VERSION
        expected = payload_model(event_type, version)
        if not isinstance(payload, expected):
            raise TypeError(f"{event_type.value} v{version} carries {expected.__name__}")
        body = payload.model_dump(mode="json")
    else:
        version = schema_version or 1
        body = payload
    event_id = uuid4()
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        schema_version=version,
        tenant_id=tenant_id,
        occurred_at=occurred_at or datetime.now(UTC),
        producer=producer,
        trace_id=trace_id,
        idempotency_key=idempotency_key or str(event_id),
        partition_key=_key_from_payload(event_type, body),
        payload=body,
    )


def parse_envelope(raw: bytes | str) -> EventEnvelope:
    """Read a message off Kafka. Raises pydantic.ValidationError naming each bad field."""
    envelope: EventEnvelope = EventEnvelope.model_validate_json(raw)
    return envelope


def typed_payload(envelope: EventEnvelope) -> ContractModel:
    """The payload as its model; UnknownPayloadVersion for a version this build does not know."""
    model = payload_model(envelope.event_type, envelope.schema_version)
    payload: ContractModel = model.model_validate(envelope.payload)
    return payload
