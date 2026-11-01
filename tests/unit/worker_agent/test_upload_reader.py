"""The upload reader's work is bounded by the header, not by a malformed row (uploads._Reader)."""

from __future__ import annotations

import pytest

from services.worker_agent import uploads

pytestmark = [pytest.mark.unit]


def test_a_row_far_wider_than_its_header_is_one_invalid_row() -> None:
    rows: list[list[object]] = [["a", "b"], ["1", "2"], ["x"] * 200_000]
    validation, ratio = uploads._validate(rows, [{"type": "number"}, {"type": "number"}])
    assert (validation["row_count"], validation["error_rows"]) == (2, 1)
    assert ratio == 0.5


def test_a_header_wider_than_the_limit_is_refused() -> None:
    header = [f"c{i}" for i in range(uploads.MAX_COLUMNS + 1)]
    with pytest.raises(ValueError, match="columns"):
        uploads._validate([header, ["1"] * len(header)], [])
