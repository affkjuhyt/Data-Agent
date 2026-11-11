"""A receipt lists its figures in their order, F2 before F10."""

from __future__ import annotations

import pytest

from services.api.modules.qa.routes import figure_order

pytestmark = pytest.mark.unit


def test_figure_keys_sort_by_their_number_not_as_text() -> None:
    keys = ["F10", "F2", "F1", "F11", "b2-0", "b10-0", "b2-1"]
    assert sorted(keys, key=figure_order) == ["F1", "F2", "F10", "F11", "b2-0", "b2-1", "b10-0"]
