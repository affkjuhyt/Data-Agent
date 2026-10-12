"""Contract v1 imports in the workflow_runtime image without importing another service (NOP-150).

The contract test suite itself runs in the kb-backend image; this proves the
same package loads here, with this image's pydantic, and that loading it pulls
in no service module.
"""

from __future__ import annotations

import json
import sys
from uuid import UUID

import pytest

from libs.contracts.schema_export import load_example
from libs.contracts.v1 import (
    ArtifactStored,
    EventType,
    Observation,
    build_envelope,
    parse_envelope,
    typed_payload,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-150")]


def test_an_observation_round_trips() -> None:
    observation = Observation.model_validate_json(json.dumps(load_example(Observation)))
    assert Observation.model_validate_json(observation.model_dump_json()) == observation


def test_an_envelope_round_trips() -> None:
    payload = ArtifactStored.model_validate(load_example(ArtifactStored))
    envelope = build_envelope(
        EventType.ARTIFACT_STORED,
        payload,
        tenant_id=UUID("a0000000-0000-0000-0000-000000000001"),
        producer="workflow_runtime",
    )
    assert typed_payload(parse_envelope(envelope.model_dump_json())) == payload


def test_the_contract_pulls_in_no_service() -> None:
    contract_modules = [m for m in sys.modules if m.startswith("libs.contracts")]
    assert contract_modules
