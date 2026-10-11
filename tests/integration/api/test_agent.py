"""API-05 agent lifecycle against PostgreSQL."""

from __future__ import annotations

import pytest

from .test_qa_query import World, build, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def agent_world(seed) -> World:
    return build(seed, "-agent")


async def test_agent_config_run_control_and_memory(agent_world: World) -> None:
    headers = {"Authorization": "Bearer kc-api-agent", "X-Data-Space": str(agent_world.space)}
    async with client(agent_world) as api:
        created = await api.post(
            "/v1/agents",
            headers=headers,
            json={
                "code": "sales-agent",
                "name": "Sales agent",
                "kind": "analysis",
                "account_user_id": str(agent_world.user),
            },
        )
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]

        config = await api.post(
            f"/v1/agents/{agent_id}/configs",
            headers=headers,
            json={"goal": "Phân tích doanh thu"},
        )
        assert config.status_code == 201, config.text
        assert config.json()["version"] == 1

        test_run = await api.post(f"/v1/agents/{agent_id}/test-runs", headers=headers)
        assert test_run.status_code == 202, test_run.text
        run_id = test_run.json()["id"]
        paused = await api.post(f"/v1/runs/{run_id}:pause", headers=headers)
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"
        resumed = await api.post(f"/v1/runs/{run_id}:resume", headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "running"

        events = await api.get(f"/v1/runs/{run_id}/events", headers=headers)
        assert events.status_code == 200, events.text
        assert "run.status" in events.text

        memory = await api.post(
            f"/v1/agents/{agent_id}/memories",
            headers=headers,
            json={"scope": "agent", "kind": "preference", "content": "Luôn hiển thị đơn vị"},
        )
        assert memory.status_code == 201, memory.text
        memory_id = memory.json()["id"]
        disabled = await api.delete(f"/v1/memories/{memory_id}", headers=headers)
        assert disabled.status_code == 200, disabled.text
