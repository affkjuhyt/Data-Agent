from __future__ import annotations

import uuid

import pytest

from services.worker_agent.questions import (
    _auto_select_single_metric,
    _drop_unsupported_cuts,
    _internal_workspace_source,
)
from services.worker_agent.understand.resolver import MetricEntry, Understanding

pytestmark = [pytest.mark.unit]


def test_workspace_dataset_uses_the_internal_query_source() -> None:
    source = _internal_workspace_source(uuid.uuid4(), uuid.uuid4())

    assert source.secret_ref == "internal://workspace"
    assert source.mode == "federated"
    assert source.status == "active"
    assert source.conn_options == {"schema": "workspace"}


def test_single_metric_suggestion_is_selected_without_a_clarification_turn() -> None:
    metric = MetricEntry(uuid.uuid4(), "SALES", "Chỉ tiêu doanh số", certified=True)
    understanding = Understanding(decision="clarify", options=[metric])

    assert _auto_select_single_metric(understanding) is True
    assert understanding.decision == "proceed"
    assert understanding.metric == metric


def test_unsupported_breakdown_is_removed_for_a_best_effort_total() -> None:
    understanding = Understanding(
        decision="proceed", group_by=["fiscal_year", "region"], filters={"fiscal_year": ["FY2026"]}
    )

    _drop_unsupported_cuts(understanding, {"fiscal_year"})

    assert understanding.group_by == ["region"]
    assert understanding.filters == {}
