"""The envelope every V2 Kafka event travels in (P0-09, 03 section 13).

It lives in libs/ rather than in services/indicator_api because the producers
are platform_workers consumers, and that image ships only backend/libs and its
own service (platform_workers/Dockerfile). Both sides import this one model, so
a message one side writes is exactly what the other side accepts.

Only the envelope is defined here. Each topic's payload is owned by the task
that introduces the topic; the envelope only requires that the payload carries
the field the topic is partitioned by.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    """The twelve POC topics of 03 section 13 (forecast.* comes after the POC)."""

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


# The payload field each topic is partitioned by. Idempotence only orders events
# within one partition (docs/v2/11-ops.md K1/K2): two events for the same
# series_key on different partitions can be processed out of order and a
# recompute can overwrite a newer value with an older one.
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
    EventType.KNOWLEDGE_PROPOSAL_CREATED: "entity_id",
    EventType.KNOWLEDGE_VERIFIED: "entity_id",
    EventType.ALERT_RAISED: "entity_id",
}


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_type: EventType
    schema_version: int = Field(ge=1, description="Version of this event type's payload")
    tenant_id: str = Field(min_length=1)
    occurred_at: AwareDatetime
    partition_key: str = Field(min_length=1)
    trace_id: str | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _key_matches_payload(self) -> Self:
        # A key that disagrees with the payload would route the event to the
        # wrong partition while looking valid, so it is refused outright.
        field = PARTITION_KEY_FIELD[self.event_type]
        expected = _key_from_payload(self.event_type, self.payload)
        if self.partition_key != expected:
            raise ValueError(
                f"partition_key {self.partition_key!r} does not match payload.{field} {expected!r}"
            )
        return self


def _key_from_payload(event_type: EventType, payload: dict[str, Any]) -> str:
    field = PARTITION_KEY_FIELD[event_type]
    value = payload.get(field)
    if value is None or str(value) == "":
        raise ValueError(f"{event_type.value} payload is missing its partition key field {field!r}")
    return str(value)


def build_envelope(
    event_type: EventType,
    *,
    tenant_id: str,
    payload: dict[str, Any],
    schema_version: int = 1,
    trace_id: str | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    """Wrap a payload; the partition key is always taken from the payload, never passed in."""
    return EventEnvelope(
        event_id=uuid4(),
        event_type=event_type,
        schema_version=schema_version,
        tenant_id=tenant_id,
        occurred_at=occurred_at or datetime.now(UTC),
        partition_key=_key_from_payload(event_type, payload),
        trace_id=trace_id,
        payload=payload,
    )


def parse_envelope(raw: bytes | str) -> EventEnvelope:
    """Read a message off Kafka. Raises pydantic.ValidationError naming each missing field."""
    return EventEnvelope.model_validate_json(raw)
