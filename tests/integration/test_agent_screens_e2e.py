"""The Agents rail against the deployed cluster: DS-BH has a team, runs, a rollout and skills.

Needs the v3-agent-seed applied (db-init, or the SQL file on its own) and the same
live login as the other e2e tests.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.conftest import live_token

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_AGENT_SPACE", "DS-BH")
MARGIN = "Biên lợi nhuận gộp"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE}


@pytest.fixture(scope="module")
def token(live_stack: dict[str, str]) -> str:
    return live_token(live_stack["keycloak"], "engineer")


@pytest.fixture(scope="module")
def kong(live_stack: dict[str, str]) -> str:
    return live_stack["kong"]


def test_agent_screens_read_the_pilot_team(kong: str, token: str) -> None:
    headers = _headers(token)
    agents = httpx.get(f"{kong}/v1/agents", headers=headers, params={"page_size": 100}, timeout=20)
    assert agents.status_code == 200, agents.text[:300]
    rows = agents.json()["items"]
    margin = next((row for row in rows if row["name"] == MARGIN), None)
    assert margin is not None, [row["name"] for row in rows]
    assert margin["goal"]
    assert margin["trigger_text"]
    assert margin["permission_text"]
    assert margin["active_deployment_id"]
    assert margin["runs_7d"]["count"] >= 1

    runs = httpx.get(f"{kong}/v1/runs", headers=headers, params={"page_size": 100}, timeout=20)
    assert runs.status_code == 200, runs.text[:300]
    statuses = {row["status"] for row in runs.json()["items"]}
    assert {"running", "waiting_human", "failed", "succeeded"} <= statuses
    assert any(row["depth"] == 1 and row["parent_run_id"] for row in runs.json()["items"])
    lead = next(row["parent_run_id"] for row in runs.json()["items"] if row["depth"] == 1)
    children = httpx.get(
        f"{kong}/v1/runs/{lead}/children", headers=headers, params={"page_size": 20}, timeout=20
    )
    assert children.status_code == 200, children.text[:300]
    assert len(children.json()["items"]) >= 2

    detail = httpx.get(f"{kong}/v1/agents/{margin['id']}", headers=headers, timeout=20)
    assert detail.status_code == 200, detail.text[:300]
    assert detail.json()["active_deployment"]["status"] == "rolling"

    skills = httpx.get(f"{kong}/v1/skills", headers=headers, params={"page_size": 100}, timeout=20)
    assert skills.status_code == 200, skills.text[:300]
    names = {row["name"] for row in skills.json()["items"]}
    assert "Giải thích biên gộp" in names
    waiting = next(row for row in skills.json()["items"] if row["name"] == "Đối chiếu công nợ")
    skill = httpx.get(f"{kong}/v1/skills/{waiting['id']}", headers=headers, timeout=20)
    assert skill.status_code == 200, skill.text[:300]
    assert any(version["status"] == "pending_review" for version in skill.json()["versions"])

    suggestions = httpx.get(
        f"{kong}/v1/skill-suggestions", headers=headers, params={"page_size": 20}, timeout=20
    )
    assert suggestions.status_code == 200, suggestions.text[:300]
    assert suggestions.json()["items"]

    tools = httpx.get(f"{kong}/v1/tools", headers=headers, params={"page_size": 20}, timeout=20)
    assert tools.status_code == 200, tools.text[:300]
    assert tools.json()["items"]
