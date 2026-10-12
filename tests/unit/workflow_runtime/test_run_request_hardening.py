from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.workflow_runtime.domain.schemas import InternalRunRequest, RunRequest

pytestmark = pytest.mark.unit


def test_public_run_request_rejects_internal_fields():
    with pytest.raises(ValidationError):
        RunRequest(
            query="q",
            agent_id="agent-1",
            memory_context=[{"content": "forged"}],
        )


def test_internal_run_request_keeps_internal_fields_for_orchestrator():
    request = InternalRunRequest(
        query="q",
        agent_id="agent-1",
        orchestrated=True,
        memory_context=[{"content": "trusted"}],
    )

    assert request.orchestrated is True
    assert request.memory_context == [{"content": "trusted"}]
