"""Event envelope v1 (NOP-150): every V2 Kafka message is one of these."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from libs.contracts.v1 import (
    PARTITION_KEY_FIELD,
    PAYLOAD_MODELS,
    ArtifactStored,
    EventEnvelope,
    EventType,
    MappingProposed,
    SourceSlaBreached,
    UnknownPayloadVersion,
    build_envelope,
    parse_envelope,
    typed_payload,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-150")]

TENANT = UUID("a0000000-0000-0000-0000-000000000001")
SOURCE = uuid4()
RELEASE = uuid4()
SHA = "ab" * 32


def stored() -> ArtifactStored:
    return ArtifactStored(source_id=SOURCE, release_id=RELEASE, artifact_sha256=SHA)


def test_the_twelve_poc_topics_each_have_a_partition_key() -> None:
    assert {t.value for t in EventType} == {
        "artifact.stored", "source.sla.breached", "extraction.completed", "mapping.proposed",
        "validation.completed", "review.case.created", "review.approved",
        "observation.published", "indicator.recomputed", "knowledge.proposal.created",
        "knowledge.verified", "alert.raised",
    }  # fmt: skip
    assert set(PARTITION_KEY_FIELD) == set(EventType)


def test_partition_keys_follow_03_section_13() -> None:
    assert PARTITION_KEY_FIELD[EventType.MAPPING_PROPOSED] == "source_id"
    assert PARTITION_KEY_FIELD[EventType.EXTRACTION_COMPLETED] == "release_id"
    assert PARTITION_KEY_FIELD[EventType.KNOWLEDGE_VERIFIED] == "object_id"


def test_input_flow_payloads_carry_release_id_and_sla_carries_overdue_since() -> None:
    for event_type in (
        EventType.ARTIFACT_STORED,
        EventType.EXTRACTION_COMPLETED,
        EventType.MAPPING_PROPOSED,
    ):
        assert "release_id" in PAYLOAD_MODELS[(event_type, 1)].model_fields
    assert {"source_id", "overdue_since"} <= set(SourceSlaBreached.model_fields)


def test_build_takes_the_key_from_the_payload_and_defaults_idempotency_to_event_id() -> None:
    envelope = build_envelope(
        EventType.ARTIFACT_STORED, stored(), tenant_id=TENANT, producer="web_crawler"
    )
    assert envelope.partition_key == str(SOURCE)
    assert envelope.idempotency_key == str(envelope.event_id)
    assert envelope.envelope_version == 1 and envelope.schema_version == 1
    assert envelope.occurred_at.tzinfo is not None


def test_round_trip_through_kafka_bytes_gives_the_typed_payload_back() -> None:
    envelope = build_envelope(
        EventType.ARTIFACT_STORED, stored(), tenant_id=TENANT, producer="web_crawler",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736", idempotency_key=f"store:{SHA}",
    )  # fmt: skip
    raw = envelope.model_dump_json().encode()
    back = parse_envelope(raw)
    assert back == envelope
    assert typed_payload(back) == stored()


def test_a_payload_of_the_wrong_model_is_refused() -> None:
    wrong = SourceSlaBreached(source_id=SOURCE, overdue_since="2026-10-08T10:30:00Z")
    with pytest.raises(TypeError, match="ArtifactStored"):
        build_envelope(EventType.ARTIFACT_STORED, wrong, tenant_id=TENANT, producer="web_crawler")


def test_a_typed_topic_refuses_a_dict_that_does_not_match_its_model() -> None:
    with pytest.raises(ValidationError, match="payload.mapping_version_id"):
        build_envelope(
            EventType.MAPPING_PROPOSED,
            {"source_id": str(SOURCE), "release_id": str(RELEASE)},
            tenant_id=TENANT, producer="series_extractor",
        )  # fmt: skip


def test_an_unknown_payload_version_is_refused_rather_than_guessed() -> None:
    envelope = build_envelope(
        EventType.ARTIFACT_STORED, stored(), tenant_id=TENANT, producer="web_crawler"
    )
    data: dict[str, Any] = envelope.model_dump(mode="json") | {"schema_version": 2}
    with pytest.raises(ValidationError, match="no payload schema_version 2"):
        EventEnvelope.model_validate(data)
    with pytest.raises(UnknownPayloadVersion):
        typed_payload(envelope.model_copy(update={"schema_version": 2}))


def test_an_untyped_topic_still_needs_its_partition_key_field() -> None:
    build_envelope(
        EventType.OBSERVATION_PUBLISHED, {"series_key": "EIA:RBRTE:D", "n": 1},
        tenant_id=TENANT, producer="indicator-api",
    )  # fmt: skip
    with pytest.raises(ValueError, match="missing its partition key field 'series_key'"):
        build_envelope(
            EventType.OBSERVATION_PUBLISHED, {"n": 1}, tenant_id=TENANT, producer="indicator-api"
        )


def test_a_key_that_disagrees_with_the_payload_is_refused() -> None:
    envelope = build_envelope(
        EventType.ARTIFACT_STORED, stored(), tenant_id=TENANT, producer="web_crawler"
    )
    data = envelope.model_dump(mode="json") | {"partition_key": str(uuid4())}
    with pytest.raises(ValidationError, match="does not match payload.source_id"):
        EventEnvelope.model_validate(data)


def test_the_envelope_is_frozen() -> None:
    envelope = build_envelope(
        EventType.MAPPING_PROPOSED,
        MappingProposed(source_id=SOURCE, release_id=RELEASE, mapping_version_id=uuid4()),
        tenant_id=TENANT, producer="series_extractor",
    )  # fmt: skip
    with pytest.raises(ValidationError):
        envelope.partition_key = "x"
