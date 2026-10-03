"""Contract v1 DTOs (NOP-150): the rules every producer and consumer relies on."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import libs.contracts.v1 as v1
from libs.contracts.schema_export import load_example
from libs.contracts.v1 import (
    HTTP_STATUS,
    PUBLIC_MODELS,
    ErrorCode,
    MappingRules,
    Observation,
    ObservationCandidate,
    ObsStatus,
    SourceConfigV2,
)
from libs.indicator.quantity import Quantity, Unit, UnitError

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-150")]

INVALID_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "contracts" / "invalid"


def example(model: type[v1.ContractModel]) -> dict[str, Any]:
    data: dict[str, Any] = load_example(model)
    return data


@pytest.mark.parametrize("model", PUBLIC_MODELS, ids=lambda m: m.__name__)
def test_example_round_trips_through_json(model: type[v1.ContractModel]) -> None:
    parsed = model.model_validate_json(json.dumps(example(model)))
    again = model.model_validate_json(parsed.model_dump_json())
    assert again == parsed


@pytest.mark.parametrize("path", sorted(INVALID_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_invalid_fixture_is_refused_at_the_named_field(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    model = getattr(v1, fixture["model"])
    with pytest.raises(ValidationError) as caught:
        model.model_validate_json(json.dumps(fixture["data"]))
    locs = [".".join(map(str, e["loc"])) for e in caught.value.errors()]
    assert fixture["loc"] in locs, f"expected an error at {fixture['loc']!r}, got {locs}"


def test_every_public_model_has_an_invalid_fixture_or_is_a_plain_ref() -> None:
    covered = {p.name.split(".")[0] for p in INVALID_DIR.glob("*.json")}
    plain = {"EntityRef", "CapabilityRef", "ReviewCaseRef", "ProfileSummary", "DryRunResult",
             "ArtifactStored", "ExtractionCompleted", "MappingProposed"}  # fmt: skip
    assert {m.__name__ for m in PUBLIC_MODELS} - covered <= plain


def test_value_is_a_decimal_string_on_the_wire() -> None:
    observation = Observation.model_validate(example(Observation))
    assert observation.value == Decimal("67.42")
    assert json.loads(observation.model_dump_json())["value"] == "67.42"


def test_an_integer_value_is_accepted_but_a_float_never() -> None:
    data = example(Observation) | {"value": 67}
    assert Observation.model_validate(data).value == Decimal(67)
    with pytest.raises(ValidationError, match="never a float"):
        Observation.model_validate(example(Observation) | {"value": 67.42})


def test_a_missing_value_carries_its_status_and_no_number() -> None:
    data = example(ObservationCandidate) | {
        "value": None,
        "obs_status": "not_published",
        "raw_text": "NA",
    }
    candidate = ObservationCandidate.model_validate(data)
    assert candidate.obs_status is ObsStatus.NOT_PUBLISHED and candidate.value is None


def test_an_instant_has_start_equal_to_end() -> None:
    data = example(ObservationCandidate)
    data |= {"event_time_end": data["event_time_start"], "period_code": "2026-09-22T15:00"}
    ObservationCandidate.model_validate(data)


def test_to_quantity_reuses_the_numeric_core() -> None:
    data = example(Observation) | {"unit": "money", "unit_multiplier": 3}
    quantity = Observation.model_validate(data).to_quantity(currency="USD")
    assert quantity == Quantity(Decimal("67420"), Unit.MONEY, 0, "USD")


def test_to_quantity_refuses_a_unit_the_numeric_core_does_not_know() -> None:
    with pytest.raises(UnitError, match="usd_per_barrel"):
        Observation.model_validate(example(Observation)).to_quantity()


def test_every_error_code_has_one_http_status_and_conflict_is_409() -> None:
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert HTTP_STATUS[ErrorCode.VERSION_CONFLICT] == 409
    assert 422 not in HTTP_STATUS.values()


def test_the_wire_keeps_the_from_key() -> None:
    config = SourceConfigV2.model_validate(example(SourceConfigV2))
    dumped = config.model_dump()
    assert dumped["release_info"]["publication_time"]["from"] == "xls_cell"
    assert "from_" not in json.dumps(dumped, default=str)
    rules = MappingRules.model_validate(
        {
            "time": {
                "from": "release_publication_time",
                "frequency": "I",
                "market_timezone": "Asia/Ho_Chi_Minh",
            },
            "values": [{"attr": "Buy", "series_key": "VCB:USDVND:BUY:I", "measure": "value"}],
        }  # fmt: skip
    )
    assert json.loads(rules.model_dump_json())["time"]["from"] == "release_publication_time"


def test_freshness_sla_is_an_iso_duration() -> None:
    config = SourceConfigV2.model_validate(example(SourceConfigV2))
    assert config.freshness_sla == timedelta(days=8)
    assert json.loads(config.model_dump_json())["freshness_sla"] == "P8D"


def test_a_document_source_needs_only_licence_and_use_rights() -> None:
    config = SourceConfigV2.model_validate(
        {"licence": "văn bản công khai", "use_rights": "internal_analysis"}
    )
    assert config.extraction_profile is None and config.purpose.value == "production"


def test_follow_link_pattern_must_compile() -> None:
    with pytest.raises(ValidationError, match="regular expression"):
        SourceConfigV2.model_validate(
            {"licence": "x", "use_rights": "blocked", "follow_link_pattern": "dieu-hanh-(gia"}
        )


def test_select_combines_with_field_but_not_two_direct_pickers() -> None:
    time = {
        "from": "release_publication_time",
        "frequency": "I",
        "market_timezone": "Asia/Ho_Chi_Minh",
    }
    pick = {"select": {"product": "VÀNG MIẾNG SJC"}, "field": "buy", "series_key": "DOJI:SJC_BAR:BUY:I",
            "measure": "value", "source_unit": "vnd_per_chi", "source_multiplier": 3}  # fmt: skip
    MappingRules.model_validate({"time": time, "values": [pick]})
    with pytest.raises(ValidationError, match="at most one of column, field, attr"):
        MappingRules.model_validate({"time": time, "values": [pick | {"attr": "Buy"}]})
