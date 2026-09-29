"""Unit tests for the P0-09 business definitions (services/indicator_api/domains).

Organised around what the contract must refuse: a number missing a required
field (BR-2, AC-3), a naive timestamp, a field nobody agreed on, and a model
whose schema does not say which definition version it is.
"""

from __future__ import annotations

import importlib
import pkgutil
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

import services.indicator_api.domains as domains
from services.indicator_api.domains.contract import ContractModel
from services.indicator_api.domains.errors import HTTP_STATUS, DomainError, ErrorCode
from services.indicator_api.domains.governance import Correction
from services.indicator_api.domains.observation import Observation, ObservationCandidate

pytestmark = pytest.mark.unit

SHA = "a" * 64

REQUIRED_OBSERVATION_FIELDS = (
    "event_time_start",
    "event_time_end",
    "market_timezone",
    "publication_time",
    "available_at",
    "system_time",
    "release_id",
    "artifact_sha256",
    "version_kind",
    "effective_version",
    "source_ref",
)


def brent_15_09() -> dict[str, Any]:
    """EIA Brent spot 15/09/2026, released 16/09 (the story's AC-3 example)."""
    return {
        "series_key": "EIA:RBRTE:D",
        "event_time_start": "2026-09-15T00:00:00-04:00",
        "event_time_end": "2026-09-15T23:59:59-04:00",
        "market_timezone": "America/New_York",
        "publication_time": "2026-09-16T10:30:00-04:00",
        "available_at": "2026-09-16T10:30:00-04:00",
        "system_time": "2026-09-16T15:00:00+00:00",
        "value": "130.80",
        "unit": "usd_per_barrel",
        "obs_status": "ok",
        "release_id": str(uuid4()),
        "artifact_sha256": SHA,
        "version_kind": "machine",
        "effective_version": 1,
        "source_ref": {"artifact": f"raw/eia/{SHA}", "sheet": "Data 1", "row": 9000, "col": "B"},
    }


def all_contract_models() -> list[type[ContractModel]]:
    for module in pkgutil.walk_packages(domains.__path__, prefix=f"{domains.__name__}."):
        importlib.import_module(module.name)
    found: list[type[ContractModel]] = []
    stack = list(ContractModel.__subclasses__())
    while stack:
        model = stack.pop()
        found.append(model)
        stack.extend(model.__subclasses__())
    return found


# ── Required fields (BR-2, AC-3) ──────────────────────────────────────────────


def test_brent_example_is_a_valid_observation():
    obs = Observation.model_validate(brent_15_09())
    assert obs.value == Decimal("130.80")


def test_missing_available_at_is_refused_and_named():
    payload = brent_15_09()
    del payload["available_at"]
    with pytest.raises(ValidationError) as exc:
        Observation.model_validate(payload)
    assert [err["loc"] for err in exc.value.errors()] == [("available_at",)]


@pytest.mark.parametrize("field", REQUIRED_OBSERVATION_FIELDS)
def test_every_required_field_is_refused_when_missing(field: str):
    payload = brent_15_09()
    del payload[field]
    with pytest.raises(ValidationError) as exc:
        Observation.model_validate(payload)
    assert (field,) in [err["loc"] for err in exc.value.errors()]


def test_candidate_has_no_effective_version_yet():
    payload = brent_15_09()
    for field in ("version_kind", "effective_version"):
        del payload[field]
    ObservationCandidate.model_validate(payload)


# ── Shapes the contract refuses ───────────────────────────────────────────────


def test_naive_timestamp_is_refused():
    payload = brent_15_09()
    payload["available_at"] = "2026-09-16T10:30:00"
    with pytest.raises(ValidationError, match="available_at"):
        Observation.model_validate(payload)


def test_unknown_field_is_refused_not_dropped():
    payload = brent_15_09()
    payload["confidence"] = 0.9
    with pytest.raises(ValidationError, match="confidence"):
        Observation.model_validate(payload)


def test_artifact_hash_must_be_sha256_hex():
    payload = brent_15_09()
    payload["artifact_sha256"] = "abc"
    with pytest.raises(ValidationError, match="artifact_sha256"):
        Observation.model_validate(payload)


def test_page_source_ref_reuses_the_docling_region():
    payload = brent_15_09()
    payload["source_ref"] = {
        "artifact": f"raw/bct/{SHA}",
        "region": {
            "page_no": 2,
            "left": 10,
            "top": 20,
            "right": 200,
            "bottom": 40,
            "page_width": 595,
            "page_height": 842,
        },
    }
    assert Observation.model_validate(payload).source_ref.region.page_no == 2  # type: ignore[union-attr]


# ── Numbers stay exact across JSON ────────────────────────────────────────────


def test_decimal_survives_a_json_round_trip():
    obs = Observation.model_validate(brent_15_09())
    again = Observation.model_validate_json(obs.model_dump_json())
    assert again.value == Decimal("130.80")
    assert '"value":"130.80"' in obs.model_dump_json()


# ── Definition versions (P0-09 section 6, C8) ─────────────────────────────────


def test_every_contract_model_stamps_its_schema_version():
    models = all_contract_models()
    assert len(models) >= 15
    for model in models:
        assert model.model_json_schema()["x-schema-version"] == model.SCHEMA_VERSION, model.__name__


def test_bumped_version_appears_in_the_schema():
    class Bumped(ContractModel):
        SCHEMA_VERSION = 2
        x: int

    assert Bumped.model_json_schema()["x-schema-version"] == 2


# ── Governance and errors ─────────────────────────────────────────────────────


def test_correction_changes_exactly_one_thing():
    base = {
        "correction_id": uuid4(),
        "case_id": uuid4(),
        "target_ref": "obs:1",
        "reason_code": "TYPO",
        "author": "maker",
        "created_at": "2026-09-29T09:00:00+07:00",
    }
    Correction.model_validate({**base, "new_value": "130.8"})
    for bad in ({}, {"new_value": "130.8", "new_mapping_version": 3}):
        with pytest.raises(ValidationError, match="exactly one"):
            Correction.model_validate({**base, **bad})


def test_story_error_list_is_exact_and_every_code_has_a_status():
    assert {code.value for code in ErrorCode} == {
        "NO_DATA_FOR_PERIOD",
        "FREQUENCY_NOT_AVAILABLE",
        "SERIES_NOT_APPROVED",
        "STALE_SOURCE",
        "ACCESS_DENIED",
        "INSUFFICIENT_EVIDENCE",
        "UNRESOLVED_ENTITY",
        "VERSION_CONFLICT",
    }
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert HTTP_STATUS[ErrorCode.VERSION_CONFLICT] == 409


def test_version_conflict_carries_the_current_version():
    err = DomainError(ErrorCode.VERSION_CONFLICT, "stale", current_version=4)
    assert (err.code, err.current_version) == (ErrorCode.VERSION_CONFLICT, 4)
