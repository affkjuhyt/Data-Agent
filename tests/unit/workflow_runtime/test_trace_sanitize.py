"""A node trace is stored in the JSONB column "AgentTraces".input/output.

With Memory on, the reranker's output carries the retrieved memories, whose
ids are UUIDs. json.dumps raised on them, and the exception aborted the rest of
the turn's persistence: later traces and the memory extraction request were
lost (dev cluster, 02/10/2026).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.workflow_runtime.infrastructure.db import _sanitize_trace

pytestmark = pytest.mark.unit


def test_values_json_cannot_encode_become_strings_at_any_depth() -> None:
    memory_id = uuid.uuid4()
    output = {
        "results": [{"id": memory_id, "score": Decimal("0.9"), "tags": [memory_id]}],
        "at": datetime(2026, 10, 2, tzinfo=UTC),
        "pair": (memory_id, 1),
    }

    sanitized = json.loads(json.dumps(_sanitize_trace(output)))

    assert sanitized["results"] == [
        {"id": str(memory_id), "score": "0.9", "tags": [str(memory_id)]}
    ]
    assert sanitized["at"] == "2026-10-02 00:00:00+00:00"
    assert sanitized["pair"] == [str(memory_id), 1]


def test_secrets_are_still_redacted_and_json_values_kept() -> None:
    sanitized = _sanitize_trace({"token": uuid.uuid4(), "n": 3, "ok": True, "none": None})

    assert sanitized == {"token": "[REDACTED]", "n": 3, "ok": True, "none": None}
