from __future__ import annotations

import uuid

from services.worker_agent.questions import _internal_workspace_source


def test_workspace_dataset_uses_the_internal_query_source() -> None:
    source = _internal_workspace_source(uuid.uuid4(), uuid.uuid4())

    assert source.secret_ref == "internal://workspace"
    assert source.mode == "federated"
    assert source.status == "active"
    assert source.conn_options == {"schema": "workspace"}
