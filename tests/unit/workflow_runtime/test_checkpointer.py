from pathlib import Path

import pytest

from services.workflow_runtime.infrastructure.checkpointer import normalize_checkpointer_dsn

pytestmark = pytest.mark.unit


def test_normalize_checkpointer_dsn_removes_sqlalchemy_driver() -> None:
    assert normalize_checkpointer_dsn(
        "postgresql+asyncpg://runtime:pw@db/dataagent"
    ) == "postgresql://runtime:pw@db/dataagent"


def test_normalize_checkpointer_dsn_preserves_plain_postgres_url() -> None:
    url = "postgresql://runtime:pw@db/dataagent"
    assert normalize_checkpointer_dsn(url) == url


def test_runtime_runner_no_longer_uses_memory_checkpointer() -> None:
    source = Path(
        "backend/services/workflow_runtime/application/runtime_services/conversation_runner.py"
    ).read_text(encoding="utf-8")
    assert "MemorySaver" not in source


def test_missing_checkpointer_dsn_is_rejected() -> None:
    with pytest.raises(ValueError, match="RUNTIME_DB_URL"):
        normalize_checkpointer_dsn("")
