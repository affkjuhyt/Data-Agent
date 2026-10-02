"""An agent's responder model is the LiteLLM alias the picker lists.

The picker offered GET /api/llm-providers entries (LiteLLM aliases) while the
draft stored a UUID into the "LLMProviders" table: update_agent_draft called
UUID() on the alias and answered 500. AgentVersions.responder_model now holds
the alias, and the runtime turns it into the model name litellm's SDK needs.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from services.workflow_runtime.domain.llm_models import sdk_model_name
from services.workflow_runtime.infrastructure import authoring_repository

pytestmark = pytest.mark.unit

AGENT = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT = "a0000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("responder", "openai/responder"),
        ("openai/responder", "openai/responder"),
        ("anthropic/claude", "anthropic/claude"),
        (None, "openai/default"),
        ("", "openai/default"),
    ],
)
def test_sdk_model_name(alias: str | None, expected: str) -> None:
    assert sdk_model_name(alias, "openai/default") == expected


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.session = _Session()

    @asynccontextmanager
    async def get_client(self, **_: Any):
        yield self.session


def _draft_update_params(client: _Client) -> dict[str, Any]:
    [statement] = client.session.statements
    return statement.compile(dialect=postgresql.dialect()).params


def test_the_picked_alias_is_stored_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    monkeypatch.setattr(authoring_repository, "client", client)

    asyncio.run(
        authoring_repository.update_agent_draft(
            AGENT, {"responder_model": "responder"}, tenant_id=TENANT
        )
    )

    assert _draft_update_params(client)["responder_model"] == "responder"
