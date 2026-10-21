"""Eval sets read back (UC-07-06, UC-13-04): GET /v1/eval-sets and GET /v1/eval-sets/{id} against
PostgreSQL, on the organisation of test_inbox_eval.py plus a member without `ask`.

The first Data Space holds a context set with three runs (an old completed one, the newest
completed one with a miss, and one still running) and a skill set; the second Data Space holds a
set of its own; a pack set belongs to no Data Space.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from .conftest import Seed
from .test_inbox_eval import Team, _user, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def team(seed: Seed) -> Team:
    org = seed.org()
    ask = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    none = seed.row("org.role", org_id=org, code="VIEW", name="View", capabilities=["view_detail"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-EV", name="EV", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-EV-B", name="EV B", steward_id=keeper)
    users = {
        "asker": _user(seed, org, "asker", ask, [space, other]),
        "nocap": _user(seed, org, "nocap", none, [space]),
    }
    return Team(org=org, space=space, other_space=other, users=users, seed=seed)


def _set(team: Team, name: str, **values: Any) -> uuid.UUID:
    values.setdefault("data_space_id", team.space)
    values.setdefault("subject_type", "context")
    return team.seed.row("eval.eval_set", org_id=team.org, name=name, **values)


def _item(team: Team, eval_set: uuid.UUID, question: str, **values: Any) -> uuid.UUID:
    return team.seed.row(
        "eval.eval_item",
        org_id=team.org,
        eval_set_id=eval_set,
        input=Json({"q": question}),
        expected=Json({"value": 1}),
        **values,
    )


def _run(team: Team, eval_set: uuid.UUID, started_at: str, **values: Any) -> uuid.UUID:
    return team.seed.row(
        "eval.eval_run",
        org_id=team.org,
        eval_set_id=eval_set,
        subject_version="context@current",
        trigger="nightly",
        started_at=started_at,
        **values,
    )


def _result(team: Team, run: uuid.UUID, item: uuid.UUID, passed: bool) -> uuid.UUID:
    return team.seed.row(
        "eval.eval_result",
        org_id=team.org,
        eval_run_id=run,
        eval_item_id=item,
        passed=passed,
        actual=Json({"value": 1 if passed else 2}),
    )


async def test_eval_sets_are_listed_by_subject(team: Team) -> None:
    context = _set(team, "Bộ chuẩn")
    agent = uuid.uuid4()
    skill = _set(team, "Bộ kỹ năng", subject_type="skill", subject_id=agent)
    pack = _set(
        team, "Bộ gói ngành", subject_type="pack", subject_id=uuid.uuid4(), data_space_id=None
    )
    _set(team, "Bộ khác", data_space_id=team.other_space)
    _item(team, context, "a")
    _item(team, context, "b")
    _item(team, context, "c", status="retired")
    run = _run(team, context, "2026-10-01T00:00:00Z", status="completed", passed=2, item_count=2)
    async with client(team) as api:
        listed = await api.get("/v1/eval-sets", headers=team.headers("asker"))
        assert listed.status_code == 200, listed.text
        by_id = {s["id"]: s for s in listed.json()["items"]}
        # The Data Space's sets and the pack set of no Data Space; not the other space's.
        assert set(by_id) == {str(context), str(skill), str(pack)}
        assert by_id[str(context)]["item_count"] == 2
        assert by_id[str(context)]["latest_run"]["id"] == str(run)
        assert by_id[str(skill)]["latest_run"] is None
        only = await api.get(
            "/v1/eval-sets", params={"subject_type": "context"}, headers=team.headers("asker")
        )
        assert [s["id"] for s in only.json()["items"]] == [str(context)]
        subject = await api.get(
            "/v1/eval-sets", params={"subject_id": str(agent)}, headers=team.headers("asker")
        )
        assert [s["id"] for s in subject.json()["items"]] == [str(skill)]
        first = await api.get(
            "/v1/eval-sets", params={"page_size": 2}, headers=team.headers("asker")
        )
        token = first.json()["next_page_token"]
        rest = await api.get(
            "/v1/eval-sets", params={"page_size": 2, "cursor": token}, headers=team.headers("asker")
        )
        seen = [s["id"] for s in first.json()["items"] + rest.json()["items"]]
        assert sorted(seen) == sorted(by_id) and rest.json()["next_page_token"] == ""
        assert (await api.get("/v1/eval-sets", headers=team.headers("nocap"))).status_code == 403


async def test_one_eval_set_shows_its_runs_and_the_misses_of_the_newest_judged_run(
    team: Team,
) -> None:
    context = _set(team, "Bộ chuẩn")
    right = _item(team, context, "Dư nợ T9?")
    wrong = _item(team, context, "Nợ xấu miền Bắc?")
    old = _run(team, context, "2026-09-01T00:00:00Z", status="completed", passed=1, failed=1)
    _result(team, old, right, False)
    newest = _run(team, context, "2026-10-01T00:00:00Z", status="completed", passed=1, failed=1)
    _result(team, newest, right, True)
    missed = _result(team, newest, wrong, False)
    running = _run(team, context, "2026-10-02T00:00:00Z")
    async with client(team) as api:
        got = await api.get(f"/v1/eval-sets/{context}", headers=team.headers("asker"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["name"] == "Bộ chuẩn" and body["item_count"] == 2
        assert (
            body["latest_run"]["id"] == str(running) and body["latest_run"]["status"] == "running"
        )
        assert [r["id"] for r in body["runs"]] == [str(running), str(newest), str(old)]
        # The running run has judged nothing yet: the misses are the newest completed run's.
        assert body["failures_run_id"] == str(newest)
        (failure,) = body["failures"]
        assert failure["result"]["id"] == str(missed) and failure["result"]["passed"] is False
        assert failure["item"]["id"] == str(wrong) and failure["item"]["input"] == {
            "q": "Nợ xấu miền Bắc?"
        }

        elsewhere = _set(team, "Bộ khác", data_space_id=team.other_space)
        hidden = await api.get(f"/v1/eval-sets/{elsewhere}", headers=team.headers("asker"))
        assert hidden.status_code == 404 and hidden.json()["code"] == "not_found"
        there = await api.get(
            f"/v1/eval-sets/{elsewhere}", headers=team.headers("asker", team.other_space)
        )
        assert there.status_code == 200 and there.json()["runs"] == []
        assert there.json()["failures_run_id"] is None
        missing = await api.get(f"/v1/eval-sets/{uuid.uuid4()}", headers=team.headers("asker"))
        assert missing.status_code == 404
        denied = await api.get(f"/v1/eval-sets/{context}", headers=team.headers("nocap"))
        assert denied.status_code == 403


async def test_runs_of_an_organisation_wide_set_are_not_shown(team: Team) -> None:
    # eval_run has no Data Space, so a pack set's run may have run on another Data Space's data:
    # its answers must not reach readers of this Data Space through the set.
    pack = _set(
        team, "Bộ của gói", data_space_id=None, subject_type="pack", subject_id=uuid.uuid4()
    )
    item = _item(team, pack, "Lãi thuần chi nhánh B?")
    run = _run(team, pack, "2026-10-01T00:00:00Z", status="completed", passed=0, failed=1)
    _result(team, run, item, False)
    async with client(team) as api:
        got = await api.get(f"/v1/eval-sets/{pack}", headers=team.headers("asker"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["item_count"] == 1 and body["latest_run"] is None
        assert body["runs"] == [] and body["failures"] == [] and body["failures_run_id"] is None
        listed = await api.get("/v1/eval-sets?subject_type=pack", headers=team.headers("asker"))
        assert listed.status_code == 200, listed.text
        (row,) = (r for r in listed.json()["items"] if r["id"] == str(pack))
        assert row["latest_run"] is None
        direct = await api.get(f"/v1/eval-runs/{run}", headers=team.headers("asker"))
        assert direct.status_code == 404


async def test_an_answer_is_read_only_by_whom_it_was_answered_as(team: Team) -> None:
    """A result's answer was computed with someone's rights (eval_runs.run_eval records whose):
    anyone else reads its scores but not the answer, and only a steward reads what was expected."""
    steward_role = team.seed.row(
        "org.role", org_id=team.org, code="STW", name="Steward", capabilities=["ask", "steward"]
    )
    team.users["steward"] = _user(team.seed, team.org, "steward", steward_role, [team.space])
    context = _set(team, "Bộ chuẩn")
    mine = _item(team, context, "Dư nợ miền Bắc?")
    theirs = _item(team, context, "Dư nợ miền Nam?")
    run = _run(team, context, "2026-10-01T00:00:00Z", status="completed", failed=2)
    for item, asker in ((mine, team.users["steward"]), (theirs, team.users["asker"])):
        team.seed.row(
            "eval.eval_result",
            org_id=team.org,
            eval_run_id=run,
            eval_item_id=item,
            passed=False,
            actual=Json({"text": "Dư nợ 1.250 tỷ", "answered_as": str(asker)}),
        )
    async with client(team) as api:
        as_steward = await api.get(f"/v1/eval-runs/{run}", headers=team.headers("steward"))
        assert as_steward.status_code == 200, as_steward.text
        actual = {r["eval_item_id"]: r["actual"] for r in as_steward.json()["results"]}
        assert actual[str(mine)]["text"] == "Dư nợ 1.250 tỷ" and actual[str(theirs)] is None
        as_asker = await api.get(f"/v1/eval-runs/{run}", headers=team.headers("asker"))
        actual = {r["eval_item_id"]: r["actual"] for r in as_asker.json()["results"]}
        assert actual[str(mine)] is None and actual[str(theirs)]["text"] == "Dư nợ 1.250 tỷ"

        steward_view = await api.get(f"/v1/eval-sets/{context}", headers=team.headers("steward"))
        assert {f["item"]["expected"]["value"] for f in steward_view.json()["failures"]} == {1}
        asker_view = await api.get(f"/v1/eval-sets/{context}", headers=team.headers("asker"))
        failures = asker_view.json()["failures"]
        assert len(failures) == 2 and all(f["item"]["expected"] is None for f in failures)


async def test_a_result_without_whom_it_was_answered_as_shows_no_answer(team: Team) -> None:
    context = _set(team, "Bộ chuẩn")
    item = _item(team, context, "Dư nợ T9?")
    run = _run(team, context, "2026-10-01T00:00:00Z", status="completed", passed=1)
    _result(team, run, item, True)  # written before answered_as was recorded
    async with client(team) as api:
        got = await api.get(f"/v1/eval-runs/{run}", headers=team.headers("asker"))
        assert got.status_code == 200 and got.json()["results"][0]["actual"] is None
