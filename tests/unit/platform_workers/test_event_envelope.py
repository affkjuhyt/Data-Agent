"""The shared V2 event envelope (backend/libs/shared/event_envelope.py, P0-09).

Runs in the platform-workers image on purpose: the workers are the producers,
and this proves their image can import and build the envelope.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from libs.shared.event_envelope import (
    PARTITION_KEY_FIELD,
    EventEnvelope,
    EventType,
    build_envelope,
    parse_envelope,
)

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"


def payload_for(event_type: EventType) -> dict[str, Any]:
    return {PARTITION_KEY_FIELD[event_type]: f"key-of-{event_type.value}", "n": 1}


def test_there_are_exactly_the_twelve_poc_topics():
    assert {t.value for t in EventType} == {
        "artifact.stored", "source.sla.breached", "extraction.completed", "mapping.proposed",
        "validation.completed", "review.case.created", "review.approved",
        "observation.published", "indicator.recomputed", "knowledge.proposal.created",
        "knowledge.verified", "alert.raised",
    }  # fmt: skip
    assert set(PARTITION_KEY_FIELD) == set(EventType)


def test_story_partition_keys():
    assert set(PARTITION_KEY_FIELD.values()) == {
        "source_id", "release_id", "series_key", "case_id", "entity_id",
    }  # fmt: skip
    assert PARTITION_KEY_FIELD[EventType.OBSERVATION_PUBLISHED] == "series_key"


@pytest.mark.parametrize("event_type", list(EventType))
def test_every_topic_builds_and_round_trips(event_type: EventType):
    env = build_envelope(event_type, tenant_id=TENANT, payload=payload_for(event_type))
    assert env.partition_key == f"key-of-{event_type.value}"
    assert parse_envelope(env.model_dump_json()) == env


def test_key_is_taken_from_the_payload_not_passed_in():
    with pytest.raises(TypeError):
        build_envelope(  # type: ignore[call-arg]
            EventType.OBSERVATION_PUBLISHED,
            tenant_id=TENANT,
            payload={"series_key": "EIA:RBRTE:D"},
            partition_key="something-else",
        )


def test_payload_without_its_key_field_is_refused():
    with pytest.raises(ValueError, match="series_key"):
        build_envelope(EventType.OBSERVATION_PUBLISHED, tenant_id=TENANT, payload={"x": 1})


def test_parsed_key_that_disagrees_with_payload_is_refused():
    env = build_envelope(
        EventType.OBSERVATION_PUBLISHED, tenant_id=TENANT, payload={"series_key": "EIA:RBRTE:D"}
    )
    raw = json.loads(env.model_dump_json())
    raw["partition_key"] = "EIA:RWTC:D"
    with pytest.raises(ValidationError, match="does not match"):
        EventEnvelope.model_validate(raw)


@pytest.mark.parametrize(
    "field", ["event_id", "event_type", "schema_version", "tenant_id", "occurred_at", "payload"]
)
def test_missing_required_field_is_refused_and_named(field: str):
    env = build_envelope(EventType.REVIEW_APPROVED, tenant_id=TENANT, payload={"case_id": "c-1"})
    raw = json.loads(env.model_dump_json())
    del raw[field]
    with pytest.raises(ValidationError) as exc:
        parse_envelope(json.dumps(raw))
    assert (field,) in [err["loc"] for err in exc.value.errors()]


def test_unknown_topic_and_unknown_field_are_refused():
    env = build_envelope(EventType.ALERT_RAISED, tenant_id=TENANT, payload={"entity_id": "BRENT"})
    raw = json.loads(env.model_dump_json())
    with pytest.raises(ValidationError):
        parse_envelope(json.dumps({**raw, "event_type": "forecast.requested"}))
    with pytest.raises(ValidationError, match="priority"):
        parse_envelope(json.dumps({**raw, "priority": "high"}))


def test_naive_occurred_at_is_refused():
    env = build_envelope(EventType.ALERT_RAISED, tenant_id=TENANT, payload={"entity_id": "BRENT"})
    raw = json.loads(env.model_dump_json())
    raw["occurred_at"] = "2026-09-29T10:00:00"
    with pytest.raises(ValidationError, match="occurred_at"):
        parse_envelope(json.dumps(raw))
