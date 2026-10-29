"""Agent group (API-05) without a database: the module's own rules (run state machine, BR-70,
BR-76, BR-170, the trigger preview) and the request checks that answer before any query.

Acceptance criteria are named in a comment above the test that proves them (tests/conftest.py's
`req` marker does not accept AC-xx-yy-zz ids yet)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient

from libs import cron
from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel.problems import Problem
from services.api.modules.agent import rules

from .conftest import ORG, SPACE, FakeIdentities, FakeVerifier, bearer

pytestmark = pytest.mark.unit

BUILDER = uuid.UUID("01920000-0000-7000-8000-0000000000b1")
TOKENS = {
    "builder": {"sub": "kc-builder", "org_id": str(ORG)},
    "asker": {"sub": "kc-asker", "org_id": str(ORG)},
}
USERS = {
    "kc-builder": (BUILDER, frozenset({"ask", "create_agent", "run_agent"}), frozenset()),
    "kc-asker": (uuid.uuid4(), frozenset({"ask"}), frozenset()),
}
AGENT = "/v1/agents/01920000-0000-7000-8000-0000000000a1"


@pytest.fixture
def api() -> TestClient:
    app = create_app(
        Settings(), token_verifier=FakeVerifier(TOKENS), identity_resolver=FakeIdentities(USERS)
    )
    return TestClient(app, raise_server_exceptions=False)


def builder() -> dict[str, str]:
    return {**bearer("builder"), "X-Data-Space": str(SPACE)}


def code_of(problem: pytest.ExceptionInfo[Problem]) -> tuple[int, str]:
    return problem.value.status, problem.value.code


# ---------------------------------------------------------------- rules


def test_run_state_machine() -> None:
    rules.transition("running", rules.PAUSABLE, "pause")
    for status in ("paused", "succeeded", "failed", "cancelled"):
        with pytest.raises(Problem) as refused:
            rules.transition(status, rules.PAUSABLE, "pause")
        assert code_of(refused) == (409, "invalid_state")
    assert rules.TERMINAL_RUN.isdisjoint(rules.ACTIVE_RUN)
    assert rules.resume_status(started=True) == "running"
    assert rules.resume_status(started=False) == "queued"


# Proves AC-13-02-06.
def test_br70_write_derived_needs_a_checkpoint_unless_acting_then_reporting() -> None:
    tool = {
        "tool": "create_proposal",
        "permission_level": "write_derived",
        "requires_checkpoint": False,
    }
    with pytest.raises(Problem) as refused:
        rules.check_tools("propose_approve", [tool])
    assert code_of(refused) == (422, "business_rule_violated")
    assert "BR-70" in (refused.value.detail or "")
    rules.check_tools("act_then_report", [tool])
    rules.check_tools("propose_approve", [{**tool, "requires_checkpoint": True}])
    rules.check_tools("suggest_only", [{"tool": "query", "permission_level": "read"}])


# Proves AC-13-02-08.
def test_br170_limits_only_lower_the_ceilings() -> None:
    with pytest.raises(Problem) as refused:
        rules.check_limits({"max_steps": 70})
    assert code_of(refused) == (422, "business_rule_violated")
    rules.check_limits({"max_steps": 40, "max_tokens": 400_000, "query_timeout_s": 999})
    with pytest.raises(Problem) as bad:
        rules.check_limits({"max_duration_min": "long"})
    assert code_of(bad) == (422, "validation_failed")


# Proves AC-13-07-06.
@pytest.mark.parametrize(
    "content",
    [
        "Khach co CCCD 001203004567",
        "CMND 123 456 789 cua khach",
        "Tai khoan 0123456789",
        "So du 1.250.000.000 VND",
        "Du no 15 ty",
        "Balance 2,000 USD left",
    ],
)
def test_br76_memories_hold_no_personal_data(content: str) -> None:
    with pytest.raises(Problem) as refused:
        rules.check_memory_content(content)
    assert code_of(refused) == (422, "business_rule_violated")


@pytest.mark.parametrize(
    "content",
    [
        "Luon hien thi don vi",
        "Ky chot ngay 5 hang thang",
        "Lech tren 0,5% thi hoi",
        "Top 10 chi nhanh",
    ],
)
def test_br76_ordinary_memories_pass(content: str) -> None:
    rules.check_memory_content(content)


# Proves AC-13-03-01.
def test_schedule_preview_lists_the_next_five_runs() -> None:
    friday_noon = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=datetime.UTC)
    runs = cron.next_runs("0 7 * * 1-5", friday_noon, 5)
    assert [r.date().isoformat() for r in runs] == [
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
    ]
    assert all((r.hour, r.minute) == (7, 0) for r in runs)
    assert cron.next_runs("*/15 * * * *", friday_noon, 2) == [
        datetime.datetime(2026, 10, 9, 12, 15, tzinfo=datetime.UTC),
        datetime.datetime(2026, 10, 9, 12, 30, tzinfo=datetime.UTC),
    ]
    # Day 31 skips the months without one; Sunday may be written 7.
    assert cron.next_runs("0 0 31 * *", friday_noon, 1)[0].date().isoformat() == "2026-10-31"
    assert cron.next_runs("0 0 * * 7", friday_noon, 1)[0].date().isoformat() == "2026-10-11"


# Proves AC-13-03-04.
def test_a_trigger_firing_more_than_fifty_times_a_day_warns() -> None:
    assert cron.fires_per_day("0 7 * * 1-5") == 1
    assert cron.fires_per_day("*/15 * * * *") == 96 > rules.DAILY_FIRE_WARNING


@pytest.mark.parametrize(
    "expr", ["7am", "* * * *", "60 * * * *", "* 24 * * *", "5-1 * * * *", "*/0 * * * *"]
)
def test_bad_cron_is_refused(expr: str) -> None:
    with pytest.raises(ValueError):
        cron.check(expr)


# ---------------------------------------------------------------- requests answered before the database


def test_agent_routes_need_a_data_space(api: TestClient) -> None:
    response = api.post(
        "/v1/agents", headers=bearer("builder"), json={"code": "a", "name": "A", "kind": "report"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "data_space_required"


def test_capabilities_come_from_a2(api: TestClient) -> None:
    response = api.post(
        "/v1/agents",
        headers={**bearer("asker"), "X-Data-Space": str(SPACE)},
        json={"code": "a", "name": "A", "kind": "report"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.parametrize(
    ("method", "path", "body", "field"),
    [
        ("POST", "/v1/agents", {"name": "A", "kind": "report"}, "code"),
        ("POST", "/v1/agents", {"code": "a", "name": "A", "kind": "robot"}, "kind"),
        (
            "POST",
            "/v1/agents",
            {"code": "a", "name": "A", "kind": "report", "monthly_budget": -1},
            "monthly_budget",
        ),
        ("POST", "/v1/agents:draft-from-text", {"template_ref": None}, "description"),
        ("POST", f"{AGENT}/configs", {"data_scope": {}}, "goal"),
        (
            "POST",
            f"{AGENT}/configs",
            {"goal": "g", "tools": [{"tool": "connect_source", "permission_level": "system"}]},
            "tools.0.permission_level",
        ),
        ("PUT", f"{AGENT}/triggers", {}, "triggers"),
        (
            "PUT",
            f"{AGENT}/triggers",
            {"triggers": [{"kind": "schedule", "cron": "7am"}]},
            "triggers.0.cron",
        ),
        ("POST", f"{AGENT}/deployments", {"traffic_pct": 10}, "config_version"),
        ("PATCH", f"/v1/deployments/{uuid.uuid4()}", {"traffic_pct": 150}, "traffic_pct"),
        ("POST", f"/v1/deployments/{uuid.uuid4()}:rollback", {"to_version": 1}, "reason"),
        ("PUT", f"/v1/runs/{uuid.uuid4()}/plan", {"edit_note": "x"}, "steps"),
        (
            "POST",
            f"{AGENT}/memories",
            {"scope": "team", "kind": "decision", "content": "x"},
            "scope",
        ),
        ("PATCH", f"/v1/memories/{uuid.uuid4()}", {"status": "archived"}, "status"),
    ],
)
def test_request_shapes_are_validated(
    api: TestClient, method: str, path: str, body: dict[str, object], field: str
) -> None:
    response = api.request(method, path, headers=builder(), json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_failed"
    assert field in {e["field"] for e in response.json()["errors"]}


def test_query_parameters_are_validated(api: TestClient) -> None:
    assert api.get(f"/v1/runs/{uuid.uuid4()}?view=raw", headers=builder()).status_code == 422
    assert api.get("/v1/runs?status=sleeping", headers=builder()).status_code == 422
    assert api.get("/v1/agents?kind=robot", headers=builder()).status_code == 422
    assert api.get(f"{AGENT}/memories?scope=team", headers=builder()).status_code == 422


# Proves AC-13-07-06.
def test_personal_data_in_a_memory_is_refused_before_saving(api: TestClient) -> None:
    response = api.post(
        f"{AGENT}/memories",
        headers=builder(),
        json={"scope": "agent", "kind": "data_insight", "content": "CCCD 001203004567 no xau"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "business_rule_violated"


def test_draft_from_text_proposes_without_saving(api: TestClient) -> None:
    template = str(uuid.uuid4())
    response = api.post(
        "/v1/agents:draft-from-text",
        headers=builder(),
        json={
            "description": "So NPL voi bao cao da nop moi khi khoa so! Neu lech thi de xuat.",
            "template_ref": template,
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["goal"] == "So NPL voi bao cao da nop moi khi khoa so!"
    assert draft["template_ref"] == template
    assert draft["autonomy"] == "propose_approve"
    assert draft["limits"] == rules.LIMIT_CEILINGS
    assert all(t["permission_level"] == "read" for t in draft["tools"])
    assert draft["out_of_scope"] == []


def test_long_jobs_and_delete_are_documented_as_a3_says(api: TestClient) -> None:
    paths = api.app.openapi()["paths"]  # type: ignore[attr-defined]
    for path in ("/v1/agents/{id}/test-runs", "/v1/agents/{id}/eval-runs"):
        accepted = paths[path]["post"]["responses"]["202"]["content"]["application/json"]["schema"]
        assert accepted["$ref"].endswith("/RunAccepted")
        assert "requestBody" in paths[path]["post"]
        assert paths[path]["post"]["requestBody"].get("required") is not True
    assert "204" in paths["/v1/memories/{id}"]["delete"]["responses"]
    assert "201" in paths["/v1/agents/{id}/deployments"]["post"]["responses"]


def test_memory_visibility_follows_scope() -> None:
    from types import SimpleNamespace

    from services.api.kernel.auth import Principal
    from services.api.modules.agent.routes import _visible

    me = Principal(user_id=BUILDER, org_id=ORG, external_id="kc-builder")
    someone = uuid.uuid4()

    def memory(scope: str, user_id: uuid.UUID | None = None) -> object:
        return SimpleNamespace(scope=scope, user_id=user_id)

    assert _visible(memory("user", BUILDER), me, None)  # type: ignore[arg-type]
    assert not _visible(memory("user", someone), me, BUILDER)  # type: ignore[arg-type]
    assert _visible(memory("agent"), me, BUILDER)  # type: ignore[arg-type]
    assert not _visible(memory("agent"), me, someone)  # type: ignore[arg-type]
    assert _visible(memory("data_space"), me, someone)  # type: ignore[arg-type]
