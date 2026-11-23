"""DI-13: the extracted fields a person should confirm (low_confidence_field)."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent.documents import extraction as x

pytestmark = [pytest.mark.unit]


def _reading(values: dict, confidence: dict, required: set[str]) -> x.Reading:
    return x.Reading(
        uuid.uuid4(), uuid.uuid4(), values, confidence, [], "extracted", frozenset(required)
    )


def test_an_unchecked_value_and_a_missing_required_field_are_doubtful() -> None:
    reading = _reading(
        {"so_hop_dong": "HĐ-12", "ngay_ky": "01/02/2024", "ben_vay": None, "ghi_chu": None},
        {"so_hop_dong": 1.0, "ngay_ky": x.UNCHECKED, "ben_vay": 0.0, "ghi_chu": 0.0},
        {"so_hop_dong", "ben_vay"},
    )
    # A checked value is sure; an optional field the document leaves out is not a doubt.
    assert sorted(x.doubtful(reading)) == ["ben_vay", "ngay_ky"]
