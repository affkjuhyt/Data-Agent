import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.workflow_runtime.domain.refusals import PlannerError


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


@pytest.mark.asyncio
async def test_planner_lists_mcp_tools_and_preserves_server_tool_identity(monkeypatch):
    from services.workflow_runtime.application.nodes.model_harness import planner

    captured: dict[str, str] = {}

    async def completion(**kwargs):
        captured["system"] = kwargs["messages"][0]["content"]
        return _response(
            {
                "intent": "question",
                "language": "vi",
                "tool_calls": [
                    {
                        "type": "mcp",
                        "server_id": "srv-1",
                        "tool": "lookup",
                        "arguments": {"q": "npl"},
                    }
                ],
            }
        )

    monkeypatch.setattr(planner.litellm, "acompletion", completion)
    result = await planner.planner_node(
        {
            "query": "tra cứu npl",
            "config": {
                "mcp_tools": [
                    {
                        "server_id": "srv-1",
                        "server_name": "catalog",
                        "tool": "lookup",
                        "description": "Lookup catalog",
                        "input_schema": {"type": "object"},
                    }
                ]
            },
        }
    )

    assert "catalog/lookup" in captured["system"]
    assert result["tool_calls"][0]["server_id"] == "srv-1"
    assert result["tool_calls"][0]["tool"] == "lookup"


@pytest.mark.asyncio
async def test_planner_refuses_mcp_tool_outside_discovered_allowlist(monkeypatch):
    from services.workflow_runtime.application.nodes.model_harness import planner

    monkeypatch.setattr(
        planner.litellm,
        "acompletion",
        AsyncMock(
            return_value=_response(
                {
                    "intent": "question",
                    "language": "vi",
                    "tool_calls": [
                        {"type": "mcp", "server_id": "srv-1", "tool": "delete_all"}
                    ],
                }
            )
        ),
    )

    with pytest.raises(PlannerError):
        await planner.planner_node(
            {
                "query": "xóa dữ liệu",
                "config": {
                    "mcp_tools": [
                        {
                            "server_id": "srv-1",
                            "server_name": "catalog",
                            "tool": "lookup",
                            "description": "Lookup catalog",
                            "input_schema": {},
                        }
                    ]
                },
            }
        )
