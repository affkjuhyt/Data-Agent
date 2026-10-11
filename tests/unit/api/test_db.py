"""Per-role database URLs (ADR-28). The org scope itself is proven against
Postgres in the integration suite, once the schemas exist."""

from __future__ import annotations

import pytest

from services.api.kernel.db import Databases, role_urls_from_env

pytestmark = [pytest.mark.unit]


def test_role_urls_from_env() -> None:
    env = {
        "DATAAGENT_DB_URL__SVC_QA": "postgresql+asyncpg://svc_qa@db/dataagent",
        "DATAAGENT_DB_URL__SVC_INBOX": "postgresql+asyncpg://svc_inbox@db/dataagent",
        "DATAAGENT_DB_URL__SVC_EMPTY": "",
        "OTHER": "x",
    }
    assert role_urls_from_env(env) == {
        "svc_qa": "postgresql+asyncpg://svc_qa@db/dataagent",
        "svc_inbox": "postgresql+asyncpg://svc_inbox@db/dataagent",
    }


def test_unconfigured_role_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="DATAAGENT_DB_URL__SVC_QA"):
        Databases({}).engine("svc_qa")
