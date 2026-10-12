"""Kỹ năng (P03, UC-03-01..07, A2 group "Kỹ năng") against PostgreSQL.

Each test builds its own organisation: two authors (ask + create_skill), a steward who also
approves (AC03), an analyst who only asks, and a user without `ask`, all members of Data Space A;
a third author is a member of Data Space B only. Workers are not running: a run, a test or a
review is asserted as its outbox event, and what a consumer would write back (an eval run that
passed, a review decision) is written here as the admin.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

SCHEMA = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "format": "date"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["period"],
}
STEPS = [
    {"kind": "query", "metric": "npl_ratio", "op": "by_region"},
    {"kind": "python", "op": "decompose"},
]


@dataclass
class Org:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    metric_version: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM skill.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            value = row[0] if row else None
            # Ids compare as the strings the api returns.
            return str(value) if isinstance(value, uuid.UUID) else value

    def run(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])

    def passed(self, version_id: object, cases: int = 3) -> None:
        """What the eval-result consumer writes when the version's tests pass (UC-03-04): a
        completed eval run with `cases` passing cases, recorded on the version."""
        eval_set = self.seed.row(
            "eval.eval_set",
            org_id=self.org,
            subject_type="skill",
            subject_id=uuid.uuid4(),
            name="Ca kiểm thử kỹ năng",
        )
        run = self.seed.row(
            "eval.eval_run",
            org_id=self.org,
            eval_set_id=eval_set,
            subject_version="skill v1",
            trigger="manual",
            item_count=cases,
            passed=cases,
            status="completed",
        )
        self.run("UPDATE skill.skill_version SET eval_run_id = %s WHERE id = %s", run, version_id)

    def approve(self, version_id: object) -> None:
        """What accepting the skill_publish proposal does (inbox → skill consumer, UC-03-05)."""
        self.run(
            "UPDATE skill.skill_version SET status = 'published', approved_by = %s,"
            " approved_at = now(), eval_run_id = coalesce(eval_run_id, %s) WHERE id = %s",
            self.users["steward"],
            uuid.uuid4(),
            version_id,
        )


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, space: uuid.UUID) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    return user


@pytest.fixture
def sk(seed: Seed) -> Org:
    org = seed.org()

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row("org.role", org_id=org, code=code, name=code, capabilities=list(caps))

    author_role = role("AUTHOR", "ask", "create_skill")
    steward_role = role("STEWARD", "ask", "create_skill", "approve")
    ask_role = role("ASK", "ask")
    upload_role = role("UPLOAD", "upload")
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-HD", name="Huy động", steward_id=keeper)
    users = {
        "author": _user(seed, org, "author", author_role, space),
        "author2": _user(seed, org, "author2", author_role, space),
        "steward": _user(seed, org, "steward", steward_role, space),
        "analyst": _user(seed, org, "analyst", ask_role, space),
        "uploader": _user(seed, org, "uploader", upload_role, space),
        "far": _user(seed, org, "far", author_role, other),
    }
    metric = seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code="npl_ratio",
        owner_id=users["steward"],
        current_version=1,
        status="active",
    )
    metric_version = seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=metric,
        version=1,
        display_name="Tỷ lệ nợ xấu",
        expression="sum(no_xau) / sum(du_no)",
        period_semantics="end_of_period",
        status="certified",
        certified_by=users["steward"],
        certified_at="2026-06-01T00:00:00Z",
        valid_from="2026-06-01T00:00:00Z",
    )
    return Org(org, space, other, users, metric_version, seed)


class Tokens:
    def __init__(self, sk: Org) -> None:
        self.sk = sk

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.sk.org)}


def client(sk: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(sk))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _near(value: str, expected: datetime.datetime) -> bool:
    at = datetime.datetime.fromisoformat(value)
    return abs(at - expected) < datetime.timedelta(minutes=5)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _skill(
    api: httpx.AsyncClient,
    sk: Org,
    code: str = "phan-ra-bien-dong",
    who: str = "author",
    space: uuid.UUID | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body = {"code": code, "name": "Phân rã biến động", "category": "decomposition", **extra}
    made = await api.post("/v1/skills", json=body, headers=sk.headers(who, space))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def _version(
    api: httpx.AsyncClient, sk: Org, skill_id: str, who: str = "author", **extra: Any
) -> dict[str, Any]:
    body = {"params_schema": SCHEMA, "steps": STEPS, **extra}
    made = await api.post(f"/v1/skills/{skill_id}/versions", json=body, headers=sk.headers(who))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def _published(
    api: httpx.AsyncClient, sk: Org, code: str, scope: str = "data_space"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A skill of the author with version 1 published (reviewed, for a non-personal scope)."""
    made = await _skill(api, sk, code, scope=scope)
    version = await _version(api, sk, made["id"])
    if scope == "personal":
        tested = await api.post(
            f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author")
        )
        assert tested.status_code == 202, tested.text
        sk.passed(version["id"])
        out = await api.post(
            f"/v1/skill-versions/{version['id']}:publish", headers=sk.headers("author")
        )
        assert out.status_code == 200, out.text
    else:
        sk.approve(version["id"])
        sk.run("UPDATE skill.skill SET current_version = 1 WHERE id = %s", made["id"])
    return made, version


# ---------------------------------------------------------------- library


async def test_author_creates_a_skill_and_the_library_shows_it(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, description="Vì sao một chỉ số tăng", scope="data_space")
        assert made["scope"] == "data_space" and made["owner_id"] == str(sk.users["author"])
        assert made["data_space_id"] == str(sk.space) and made["origin"] == "manual"
        assert made["current_version"] is None and made["run_count_30d"] == 0
        assert "org_id" not in made and "created_by" not in made
        assert sk.one("SELECT category FROM skill.skill WHERE id = %s", made["id"]) == (
            "decomposition"
        )
        events = dict(sk.events(made["id"]))
        assert events["skill.created"]["skill_id"] == made["id"]
        assert events["skill.created"]["data_space_id"] == str(sk.space)
        assert events["skill.created"]["owner_id"] == str(sk.users["author"])

        # A3: 409 conflict on a duplicate code in the Data Space.
        again = await api.post(
            "/v1/skills",
            json={"code": made["code"], "name": "Khác", "category": "custom"},
            headers=sk.headers("author2"),
        )
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "conflict"
        # The same code in another Data Space is another skill.
        await _skill(api, sk, made["code"], who="far", space=sk.other)

        bad = await api.post(
            "/v1/skills",
            json={"code": "x", "name": "x", "category": "magic"},
            headers=sk.headers("author"),
        )
        assert bad.status_code == 422
        no_cap = await api.post(
            "/v1/skills",
            json={"code": "y", "name": "y", "category": "custom"},
            headers=sk.headers("analyst"),
        )
        assert no_cap.status_code == 403, no_cap.text

        by_id = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("analyst"))
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["versions"] == [] and by_id.json()["published_version"] is None
        by_code = await api.get(f"/v1/skills/{made['code']}", headers=sk.headers("analyst"))
        assert by_code.status_code == 200 and by_code.json()["id"] == made["id"]

        listed = await api.get("/v1/skills", headers=sk.headers("analyst"))
        assert listed.status_code == 200
        assert [s["id"] for s in listed.json()["items"]] == [made["id"]]

        no_header = await api.get("/v1/skills", headers={"Authorization": "Bearer kc-analyst"})
        assert no_header.status_code == 400
        assert no_header.json()["code"] == "data_space_required"
        no_ask = await api.get("/v1/skills", headers=sk.headers("uploader"))
        assert no_ask.status_code == 403
        no_ask_detail = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("uploader"))
        assert no_ask_detail.status_code == 403


async def test_scope_decides_who_sees_a_skill(sk: Org) -> None:
    async with client(sk) as api:
        personal = await _skill(api, sk, "rieng")
        shared = await _skill(api, sk, "chung", scope="data_space")
        org_wide = await _skill(api, sk, "toan-to-chuc", who="far", space=sk.other, scope="org")
        in_b = await _skill(api, sk, "chi-o-b", who="far", space=sk.other, scope="data_space")

        mine = await api.get("/v1/skills", headers=sk.headers("author"))
        assert {s["id"] for s in mine.json()["items"]} == {
            personal["id"],
            shared["id"],
            org_wide["id"],
        }
        # Someone else's personal skill does not exist for the analyst.
        theirs = await api.get("/v1/skills", headers=sk.headers("analyst"))
        assert {s["id"] for s in theirs.json()["items"]} == {shared["id"], org_wide["id"]}
        hidden = await api.get(f"/v1/skills/{personal['id']}", headers=sk.headers("analyst"))
        assert hidden.status_code == 404
        # Data Space isolation: B's skill is not reachable from A, nor A's from B.
        assert (
            await api.get(f"/v1/skills/{in_b['id']}", headers=sk.headers("author"))
        ).status_code == 404
        from_b = await api.get(f"/v1/skills/{shared['id']}", headers=sk.headers("far", sk.other))
        assert from_b.status_code == 404
        # Not a member of B: the header itself answers 404.
        not_member = await api.get("/v1/skills", headers=sk.headers("author", sk.other))
        assert not_member.status_code == 404

        # Filters and keyset pages.
        scoped = await api.get("/v1/skills?scope=org", headers=sk.headers("author"))
        assert [s["id"] for s in scoped.json()["items"]] == [org_wide["id"]]
        found = await api.get("/v1/skills?q=RIENG", headers=sk.headers("author"))
        assert [s["id"] for s in found.json()["items"]] == [personal["id"]]
        none = await api.get("/v1/skills?category=forecast", headers=sk.headers("author"))
        assert none.json()["items"] == []
        first = await api.get("/v1/skills?page_size=2", headers=sk.headers("author"))
        token = first.json()["next_page_token"]
        assert len(first.json()["items"]) == 2 and token
        rest = await api.get(
            f"/v1/skills?page_size=2&page_token={token}", headers=sk.headers("author")
        )
        assert len(rest.json()["items"]) == 1 and rest.json()["next_page_token"] == ""


async def test_a_code_names_the_data_spaces_own_skill_before_an_org_wide_one(sk: Org) -> None:
    async with client(sk) as api:
        # The org-wide one is older and was authored in B; from A the code is A's skill.
        await _skill(api, sk, "doi-chieu", who="far", space=sk.other, scope="org")
        local = await _skill(api, sk, "doi-chieu", scope="data_space")
        got = await api.get("/v1/skills/doi-chieu", headers=sk.headers("analyst"))
        assert got.status_code == 200
        assert got.json()["id"] == local["id"]


# ---------------------------------------------------------------- versions


async def test_versions_are_numbered_and_only_a_draft_is_edited(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, scope="data_space")
        v1 = await _version(
            api,
            sk,
            made["id"],
            template_md="{{region}} đóng góp nhiều nhất",
            metric_version_ids=[str(sk.metric_version)],
            sandbox_limits={"cpu": 2, "memory_mb": 4096, "timeout_s": 120},
        )
        assert v1["version"] == 1 and v1["status"] == "draft"
        assert v1["metric_version_ids"] == [str(sk.metric_version)]
        assert v1["sandbox_limits"] == {
            "cpu": 2,
            "memory_mb": 4096,
            "timeout_s": 120,
            "network": False,
        }
        events = dict(sk.events(v1["id"]))
        assert events["skill_version.created"]["skill_id"] == made["id"]
        assert events["skill_version.created"]["version"] == 1
        v2 = await _version(api, sk, made["id"])
        assert v2["version"] == 2

        url = f"/v1/skills/{made['id']}/versions"
        body = {"params_schema": SCHEMA, "steps": STEPS}
        # BR-94: a step gets at most 120 s, 2 vCPU, 4 GB, and never the network.
        for limits in ({"timeout_s": 150}, {"cpu": 4}, {"memory_mb": 8192}, {"network": True}):
            over = await api.post(
                url, json={**body, "sandbox_limits": limits}, headers=sk.headers("author")
            )
            assert over.status_code == 422, limits
        unusable = await api.post(
            url,
            json={**body, "params_schema": {"type": "object", "oneOf": []}},
            headers=sk.headers("author"),
        )
        assert unusable.status_code == 422
        assert unusable.json()["code"] == "validation_failed"
        unknown = await api.post(
            url,
            json={**body, "metric_version_ids": [str(uuid.uuid4())]},
            headers=sk.headers("author"),
        )
        assert unknown.status_code == 422
        assert unknown.json()["errors"][0]["field"] == "metric_version_ids"
        no_steps = await api.post(url, json={**body, "steps": []}, headers=sk.headers("author"))
        assert no_steps.status_code == 422
        # Only the owner changes a skill; the capability alone is not enough.
        not_owner = await api.post(url, json=body, headers=sk.headers("author2"))
        assert not_owner.status_code == 403
        no_cap = await api.post(url, json=body, headers=sk.headers("analyst"))
        assert no_cap.status_code == 403

        patch = f"/v1/skill-versions/{v1['id']}"
        edited = await api.patch(
            patch,
            json={"template_md": None, "steps": [{"kind": "query", "metric": "npl_ratio"}]},
            headers=sk.headers("author"),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["template_md"] is None and len(edited.json()["steps"]) == 1
        assert sk.one("SELECT template_md FROM skill.skill_version WHERE id = %s", v1["id"]) is None
        null_steps = await api.patch(patch, json={"steps": None}, headers=sk.headers("author"))
        assert null_steps.status_code == 422
        bad_schema = await api.patch(
            patch, json={"params_schema": {"type": "array"}}, headers=sk.headers("author")
        )
        assert bad_schema.status_code == 422
        assert (await api.patch(patch, json={}, headers=sk.headers("author2"))).status_code == 403
        assert (await api.patch(patch, json={}, headers=sk.headers("analyst"))).status_code == 403
        # Data Space isolation: the version is not there from B.
        far = await api.patch(patch, json={}, headers=sk.headers("far", sk.other))
        assert far.status_code == 404

        tested = await api.post(f"/v1/skill-versions/{v1['id']}:test", headers=sk.headers("author"))
        assert tested.status_code == 202
        # A3 / BR-12: what was sent to testing is not edited; a new version is.
        frozen = await api.patch(patch, json={"template_md": "x"}, headers=sk.headers("author"))
        assert frozen.status_code == 409
        assert frozen.json()["code"] == "invalid_state"


async def test_test_sends_a_draft_to_testing_and_reserves_the_eval_run(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk)
        version = await _version(api, sk, made["id"])
        url = f"/v1/skill-versions/{version['id']}:test"
        assert (await api.post(url, headers=sk.headers("analyst"))).status_code == 403
        assert (await api.post(url, headers=sk.headers("author2"))).status_code == 404
        assert (await api.post(url, headers=sk.headers("far", sk.other))).status_code == 404

        tested = await api.post(url, headers=sk.headers("author"))
        assert tested.status_code == 202, tested.text
        body = tested.json()
        assert body["status"] == "queued" and body["poll_after_ms"] > 0
        assert tested.headers["Location"] == f"/v1/eval-runs/{body['id']}"
        assert sk.one("SELECT status FROM skill.skill_version WHERE id = %s", version["id"]) == (
            "testing"
        )
        events = dict(sk.events(version["id"]))
        requested = events["skill_version.eval_requested"]
        assert requested["eval_run_id"] == body["id"]
        assert requested["previous_status"] == "draft" and requested["status"] == "testing"

        again = await api.post(url, headers=sk.headers("author"))
        assert again.status_code == 409
        assert again.json()["code"] == "invalid_state"


async def test_test_of_another_owners_skill_is_refused(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, scope="data_space")
        version = await _version(api, sk, made["id"])
        refused = await api.post(
            f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author2")
        )
        assert refused.status_code == 403


# ---------------------------------------------------------------- publish


async def test_publish_needs_a_passed_test(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk)
        version = await _version(api, sk, made["id"])
        url = f"/v1/skill-versions/{version['id']}:publish"
        draft = await api.post(url, headers=sk.headers("author"))
        assert draft.status_code == 409 and draft.json()["code"] == "invalid_state"
        await api.post(f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author"))
        running = await api.post(url, headers=sk.headers("author"))
        assert running.status_code == 409, "testing without a passing eval run"
        assert (await api.post(url, headers=sk.headers("analyst"))).status_code == 403


async def test_a_personal_skill_is_published_without_review(sk: Org) -> None:
    async with client(sk) as api:
        made, v1 = await _published(api, sk, "ca-nhan", scope="personal")
        row = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("author"))
        detail = row.json()
        assert detail["current_version"] == 1
        assert detail["published_version"]["id"] == v1["id"]
        assert detail["published_version"]["approved_by"] == str(sk.users["author"])
        assert detail["published_version"]["params_schema"] == SCHEMA
        published = dict(sk.events(v1["id"]))["skill.published"]
        assert published["skill_id"] == made["id"] and published["scope"] == "personal"

        # A second version replaces the first, which keeps serving through BR-97's 30 days.
        v2 = await _version(api, sk, made["id"])
        await api.post(f"/v1/skill-versions/{v2['id']}:test", headers=sk.headers("author"))
        sk.passed(v2["id"])
        out = await api.post(f"/v1/skill-versions/{v2['id']}:publish", headers=sk.headers("author"))
        assert out.status_code == 200, out.text
        assert out.json()["status"] == "published"
        old = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("author"))
        versions = {v["version"]: v for v in old.json()["versions"]}
        assert versions[1]["status"] == "retired"
        assert _near(versions[1]["retire_at"], _now() + datetime.timedelta(days=30))
        assert old.json()["current_version"] == 2
        assert "skill_version.retired" in dict(sk.events(v1["id"]))


@pytest.mark.parametrize(("scope", "steps"), [("data_space", 1), ("org", 2)])
async def test_a_shared_scope_goes_to_review(sk: Org, scope: str, steps: int) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, scope=scope)
        version = await _version(api, sk, made["id"])
        await api.post(f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author"))
        sk.passed(version["id"])
        out = await api.post(
            f"/v1/skill-versions/{version['id']}:publish", headers=sk.headers("author")
        )
        assert out.status_code == 200, out.text
        # UC-03-05 / BR-96: a proposal in Cần bạn, two steps for the whole organisation.
        assert out.json()["status"] == "pending_review"
        assert out.json()["approval_steps"] == steps and out.json()["approved_by"] is None
        assert sk.one("SELECT current_version FROM skill.skill WHERE id = %s", made["id"]) is None
        requested = dict(sk.events(version["id"]))["skill_version.publish_requested"]
        assert requested["approval_steps"] == steps and requested["scope"] == scope
        assert requested["requested_by"] == str(sk.users["author"])
        assert requested["eval_run_id"]
        again = await api.post(
            f"/v1/skill-versions/{version['id']}:publish", headers=sk.headers("author")
        )
        assert again.status_code == 409


# AC-03-05-04
async def test_a_data_space_skill_needs_three_passing_cases(sk: Org) -> None:
    # BR-12: scope data_space or wider needs 3 passing cases; personal needs no review.
    async with client(sk) as api:
        made = await _skill(api, sk, scope="data_space")
        version = await _version(api, sk, made["id"])
        await api.post(f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author"))
        sk.passed(version["id"], cases=2)
        url = f"/v1/skill-versions/{version['id']}:publish"
        two = await api.post(url, headers=sk.headers("author"))
        assert two.status_code == 422 and two.json()["code"] == "business_rule_violated"
        assert "BR-12" in two.json()["detail"]
        sk.passed(version["id"], cases=3)
        three = await api.post(url, headers=sk.headers("author"))
        assert three.status_code == 200, three.text
        assert three.json()["status"] == "pending_review"

        personal, _ = await _published(api, sk, "ca-nhan-it-ca", scope="personal")
        assert personal["id"]


# ---------------------------------------------------------------- runs


async def test_a_run_needs_a_published_version_and_valid_params(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, scope="data_space")
        version = await _version(api, sk, made["id"])
        url = f"/v1/skills/{made['id']}/runs"
        params = {"period": "2026-09-30", "threshold": 0.05}
        unpublished = await api.post(url, json={"params": params}, headers=sk.headers("analyst"))
        assert unpublished.status_code == 409
        assert unpublished.json()["code"] == "invalid_state"

        sk.approve(version["id"])
        wrong = await api.post(
            url,
            json={"params": {"period": "30/09/2026", "threshold": 2}},
            headers=sk.headers("analyst"),
        )
        assert wrong.status_code == 422 and wrong.json()["code"] == "validation_failed"
        assert {e["field"] for e in wrong.json()["errors"]} == {
            "params.period",
            "params.threshold",
        }
        missing = await api.post(url, json={"params": {}}, headers=sk.headers("analyst"))
        assert missing.status_code == 422
        assert missing.json()["errors"][0]["field"] == "params.period"
        no_ask = await api.post(url, json={"params": params}, headers=sk.headers("uploader"))
        assert no_ask.status_code == 403
        far = await api.post(url, json={"params": params}, headers=sk.headers("far", sk.other))
        assert far.status_code == 404

        started = await api.post(
            f"/v1/skills/{made['code']}/runs",
            json={"params": params, "as_of": "2026-10-01"},
            headers=sk.headers("analyst"),
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert started.json()["status"] == "queued"
        assert started.headers["Location"] == f"/v1/skill-runs/{run_id}"
        assert sk.one("SELECT principal_id FROM skill.skill_run WHERE id = %s", run_id) == str(
            sk.users["analyst"]
        )
        requested = dict(sk.events(run_id))["skill_run.requested"]
        assert requested["skill_version_id"] == version["id"]
        assert requested["skill_id"] == made["id"] and requested["data_space_id"] == str(sk.space)

        # What worker-agent writes as the steps finish.
        sk.seed.row(
            "skill.skill_run_step",
            org_id=sk.org,
            skill_run_id=uuid.UUID(run_id),
            seq=1,
            name="Truy vấn nợ xấu theo vùng",
            status="ok",
            output=Json({"rows": 6}),
            duration_ms=420,
        )
        got = await api.get(f"/v1/skill-runs/{run_id}", headers=sk.headers("analyst"))
        assert got.status_code == 200, got.text
        run = got.json()
        assert run["skill_id"] == made["id"] and run["version"] == 1
        assert run["params"] == params and run["as_of"] == "2026-10-01"
        assert run["caller_kind"] == "user" and run["status"] == "queued"
        assert [s["name"] for s in run["steps"]] == ["Truy vấn nợ xấu theo vùng"]

        # A result is computed under its runner's rights: not readable by someone else.
        other = await api.get(f"/v1/skill-runs/{run_id}", headers=sk.headers("author"))
        assert other.status_code == 404
        no_ask_read = await api.get(f"/v1/skill-runs/{run_id}", headers=sk.headers("uploader"))
        assert no_ask_read.status_code == 403
        unknown = await api.get(f"/v1/skill-runs/{uuid.uuid4()}", headers=sk.headers("analyst"))
        assert unknown.status_code == 404


async def test_a_version_flagged_needs_review_still_runs(sk: Org) -> None:
    async with client(sk) as api:
        made, version = await _published(api, sk, "can-xem-lai")
        sk.run(
            "UPDATE skill.skill_version SET status = 'needs_review' WHERE id = %s", version["id"]
        )
        started = await api.post(
            f"/v1/skills/{made['id']}/runs",
            json={"params": {"period": "2026-09-30"}},
            headers=sk.headers("analyst"),
        )
        assert started.status_code == 202, started.text
        # UC-03-04 1a: re-tested in place; it keeps its label (and keeps running) meanwhile.
        tested = await api.post(
            f"/v1/skill-versions/{version['id']}:test", headers=sk.headers("author")
        )
        assert tested.status_code == 202
        assert sk.one("SELECT status FROM skill.skill_version WHERE id = %s", version["id"]) == (
            "needs_review"
        )


# ---------------------------------------------------------------- retire (UC-03-06)


async def test_the_approver_retires_a_published_version(sk: Org) -> None:
    async with client(sk) as api:
        made, version = await _published(api, sk, "ban-cu")
        replacement, _ = await _published(api, sk, "ban-moi")
        unpublished = await _skill(api, sk, "chua-phat-hanh", scope="data_space")
        started = await api.post(
            f"/v1/skills/{made['id']}/runs",
            json={"params": {"period": "2026-09-30"}},
            headers=sk.headers("author"),
        )
        run_id = started.json()["id"]
        url = f"/v1/skill-versions/{version['id']}:retire"
        body = {"replaced_by_skill_id": replacement["id"], "reason": "Thay bằng bản mới"}

        # UC-03-06 is AC03's: the owner without `approve` cannot.
        assert (await api.post(url, json=body, headers=sk.headers("author"))).status_code == 403
        blank = await api.post(url, json={"reason": "  "}, headers=sk.headers("steward"))
        assert blank.status_code == 422
        for bad in (made["id"], unpublished["id"], str(uuid.uuid4())):
            wrong = await api.post(
                url,
                json={"replaced_by_skill_id": bad, "reason": "x"},
                headers=sk.headers("steward"),
            )
            assert wrong.status_code == 422, bad
            assert wrong.json()["errors"][0]["field"] == "replaced_by_skill_id"
        far = await api.post(url, json=body, headers=sk.headers("far", sk.other))
        assert far.status_code == 403  # far holds create_skill, not approve

        # The steward is not the owner: retiring is the approver's call (UC-03-06 actor AC03).
        out = await api.post(url, json=body, headers=sk.headers("steward"))
        assert out.status_code == 200, out.text
        assert out.json()["status"] == "retired"
        assert _near(out.json()["retire_at"], _now() + datetime.timedelta(days=30))
        assert (
            sk.one("SELECT replaced_by_skill_id FROM skill.skill WHERE id = %s", made["id"])
            == (replacement["id"])
        )
        assert sk.one("SELECT current_version FROM skill.skill WHERE id = %s", made["id"]) is None
        events = dict(sk.events(version["id"]))
        retired = events["skill_version.retired"]
        assert retired["replaced_by_skill_id"] == replacement["id"]
        assert "reason" not in retired
        audit = events["audit.requested"]
        assert audit["action"] == "skill_version.retire"
        assert audit["actor_id"] == str(sk.users["steward"])
        assert audit["before"] == {"status": "published"}
        assert audit["after"]["reason"] == "Thay bằng bản mới"

        again = await api.post(url, json=body, headers=sk.headers("steward"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"
        # BR-97: hidden from the library, while the old run still shows how it was computed.
        listed = await api.get("/v1/skills", headers=sk.headers("analyst"))
        assert made["id"] not in {s["id"] for s in listed.json()["items"]}
        old_run = await api.get(f"/v1/skill-runs/{run_id}", headers=sk.headers("author"))
        assert old_run.status_code == 200 and old_run.json()["skill_version_id"] == version["id"]
        no_more = await api.post(
            f"/v1/skills/{made['id']}/runs",
            json={"params": {"period": "2026-09-30"}},
            headers=sk.headers("author"),
        )
        assert no_more.status_code == 409


async def test_only_a_runnable_version_is_retired(sk: Org) -> None:
    async with client(sk) as api:
        made, version = await _published(api, sk, "xem-lai")
        draft = await _version(api, sk, made["id"])
        refused = await api.post(
            f"/v1/skill-versions/{draft['id']}:retire",
            json={"reason": "x"},
            headers=sk.headers("steward"),
        )
        assert refused.status_code == 409
        sk.run(
            "UPDATE skill.skill_version SET status = 'needs_review' WHERE id = %s", version["id"]
        )
        out = await api.post(
            f"/v1/skill-versions/{version['id']}:retire",
            json={"reason": "Không đạt kiểm định lại"},
            headers=sk.headers("steward"),
        )
        assert out.status_code == 200, out.text
        # The audit record says what the version was, not what it usually is.
        assert dict(sk.events(version["id"]))["audit.requested"]["before"] == {
            "status": "needs_review"
        }


# ---------------------------------------------------------------- share (UC-03-07)


async def test_widening_a_scope_opens_a_review_and_narrowing_applies_at_once(sk: Org) -> None:
    async with client(sk) as api:
        draft_only = await _skill(api, sk, "nhap")
        refused = await api.post(
            f"/v1/skills/{draft_only['id']}:share",
            json={"scope": "data_space"},
            headers=sk.headers("author"),
        )
        assert refused.status_code == 409 and refused.json()["code"] == "invalid_state"

        made, version = await _published(api, sk, "chia-se", scope="personal")
        url = f"/v1/skills/{made['id']}:share"
        assert (
            await api.post(url, json={"scope": "org"}, headers=sk.headers("analyst"))
        ).status_code == 403
        # A personal skill is somebody else's: it is not even there for another author.
        assert (
            await api.post(url, json={"scope": "org"}, headers=sk.headers("author2"))
        ).status_code == 404
        bad = await api.post(url, json={"scope": "world"}, headers=sk.headers("author"))
        assert bad.status_code == 422

        wider = await api.post(url, json={"scope": "org"}, headers=sk.headers("author"))
        assert wider.status_code == 200, wider.text
        # The scope changes when the proposal is accepted, never before.
        assert wider.json()["scope"] == "personal"
        assert sk.one("SELECT scope FROM skill.skill WHERE id = %s", made["id"]) == "personal"
        asked = dict(sk.events(made["id"]))["skill.share_requested"]
        assert asked["skill_version_id"] == version["id"]
        assert asked["previous_scope"] == "personal" and asked["scope"] == "org"
        assert asked["approval_steps"] == 2

        same = await api.post(url, json={"scope": "personal"}, headers=sk.headers("author"))
        assert same.status_code == 200 and same.json()["scope"] == "personal"

        shared, _ = await _published(api, sk, "da-chia-se", scope="data_space")
        surl = f"/v1/skills/{shared['id']}:share"
        not_owner = await api.post(surl, json={"scope": "personal"}, headers=sk.headers("author2"))
        assert not_owner.status_code == 403
        narrower = await api.post(surl, json={"scope": "personal"}, headers=sk.headers("author"))
        assert narrower.status_code == 200 and narrower.json()["scope"] == "personal"
        assert sk.one("SELECT scope FROM skill.skill WHERE id = %s", shared["id"]) == "personal"
        done = dict(sk.events(shared["id"]))["skill.shared"]
        assert done["previous_scope"] == "data_space" and done["scope"] == "personal"
        gone = await api.get(f"/v1/skills/{shared['id']}", headers=sk.headers("analyst"))
        assert gone.status_code == 404


# ---------------------------------------------------------------- suggestions (UC-03-07)


def _suggestion(sk: Org, space: uuid.UUID | None = None, **pattern: Any) -> uuid.UUID:
    """What the nightly scan writes: 3 sessions of 2 people (UC-03-07 precondition)."""
    row = uuid.uuid4()
    sk.run(
        "INSERT INTO skill.skill_suggestion (id, org_id, data_space_id, pattern, session_ids,"
        " user_count) VALUES (%s, %s, %s, %s, %s::uuid[], 2)",
        row,
        sk.org,
        space or sk.space,
        Json(pattern),
        [str(uuid.uuid4()) for _ in range(3)],
    )
    return row


# AC-03-07-05
async def test_a_suggestion_shows_the_step_structure_only(sk: Org) -> None:
    leaky = _suggestion(
        sk,
        name="Nợ xấu theo vùng",
        category="decomposition",
        steps=[
            {"kind": "query", "metric": "npl_ratio", "op": "by_region", "rows": [["HN", 0.031]]},
            {"kind": "python", "op": "decompose", "output": {"HN": 0.4}},
        ],
        result={"text": "Hà Nội đóng góp 40%"},
    )
    _suggestion(sk, sk.other, steps=[{"kind": "query"}])
    async with client(sk) as api:
        listed = await api.get("/v1/skill-suggestions", headers=sk.headers("analyst"))
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [i["id"] for i in items] == [str(leaky)]
        assert items[0]["user_count"] == 2 and len(items[0]["session_ids"]) == 3
        assert items[0]["pattern"] == {
            "name": "Nợ xấu theo vùng",
            "category": "decomposition",
            "steps": [
                {"kind": "query", "metric": "npl_ratio", "op": "by_region"},
                {"kind": "python", "op": "decompose"},
            ],
        }
        none = await api.get(
            "/v1/skill-suggestions?status=dismissed", headers=sk.headers("analyst")
        )
        assert none.json()["items"] == []
        bad = await api.get("/v1/skill-suggestions?status=x", headers=sk.headers("analyst"))
        assert bad.status_code == 422
        no_ask = await api.get("/v1/skill-suggestions", headers=sk.headers("uploader"))
        assert no_ask.status_code == 403


# AC-03-07-02
async def test_accepting_a_suggestion_makes_a_personal_draft(sk: Org) -> None:
    suggestion = _suggestion(
        sk,
        name="Đối chiếu dư nợ",
        category="not-a-category",
        params_schema={"type": "object", "properties": {"period": {"type": "string"}}},
        steps=[{"kind": "query", "metric": "du_no", "output": [1, 2]}],
    )
    other = _suggestion(sk, sk.other, steps=[{"kind": "query"}])
    async with client(sk) as api:
        url = f"/v1/skill-suggestions/{suggestion}:accept"
        assert (await api.post(url, headers=sk.headers("analyst"))).status_code == 403
        far = await api.post(f"/v1/skill-suggestions/{other}:accept", headers=sk.headers("author"))
        assert far.status_code == 404

        out = await api.post(url, headers=sk.headers("steward"))
        assert out.status_code == 200, out.text
        made = out.json()
        assert made["origin"] == "agent_suggested" and made["origin_ref"] == str(suggestion)
        assert made["scope"] == "personal" and made["owner_id"] == str(sk.users["steward"])
        assert made["category"] == "custom" and made["name"] == "Đối chiếu dư nợ"
        detail = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("steward"))
        assert [v["status"] for v in detail.json()["versions"]] == ["draft"]
        version_id = detail.json()["versions"][0]["id"]
        assert sk.one("SELECT steps FROM skill.skill_version WHERE id = %s", version_id) == [
            {"kind": "query", "metric": "du_no"}
        ]
        assert sk.one(
            "SELECT params_schema FROM skill.skill_version WHERE id = %s", version_id
        ) == {"type": "object", "properties": {"period": {"type": "string"}}}
        assert (
            sk.one("SELECT accepted_skill_id FROM skill.skill_suggestion WHERE id = %s", suggestion)
            == made["id"]
        )
        assert "skill.created" in dict(sk.events(made["id"]))
        assert "skill_version.created" in dict(sk.events(version_id))
        accepted = dict(sk.events(suggestion))["skill_suggestion.accepted"]
        assert accepted["accepted_skill_id"] == made["id"]

        again = await api.post(url, headers=sk.headers("author"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"

        # Then UC-03-03..05 as for any draft: test, publish (personal), share → review.
        await api.post(f"/v1/skill-versions/{version_id}:test", headers=sk.headers("steward"))
        sk.passed(version_id)
        published = await api.post(
            f"/v1/skill-versions/{version_id}:publish", headers=sk.headers("steward")
        )
        assert published.json()["status"] == "published"
        shared = await api.post(
            f"/v1/skills/{made['id']}:share",
            json={"scope": "data_space"},
            headers=sk.headers("steward"),
        )
        assert shared.status_code == 200
        assert "skill.share_requested" in dict(sk.events(made["id"]))


async def test_accepting_a_suggestion_without_a_usable_schema(sk: Org) -> None:
    suggestion = _suggestion(sk, category="forecast", params_schema={"oneOf": []})
    async with client(sk) as api:
        out = await api.post(
            f"/v1/skill-suggestions/{suggestion}:accept", headers=sk.headers("author")
        )
        assert out.status_code == 200, out.text
        assert out.json()["category"] == "forecast" and out.json()["name"] == "Kỹ năng đề xuất"
        schema = sk.one(
            "SELECT params_schema FROM skill.skill_version WHERE skill_id = %s", out.json()["id"]
        )
        assert schema == {"type": "object", "properties": {}}


async def test_dismissing_a_suggestion_mutes_it_for_ninety_days(sk: Org) -> None:
    suggestion = _suggestion(sk, steps=[{"kind": "query", "result": 1}])
    other = _suggestion(sk, sk.other, steps=[{"kind": "query"}])
    async with client(sk) as api:
        url = f"/v1/skill-suggestions/{suggestion}:dismiss"
        assert (await api.post(url, headers=sk.headers("uploader"))).status_code == 403
        far = await api.post(
            f"/v1/skill-suggestions/{other}:dismiss", headers=sk.headers("analyst")
        )
        assert far.status_code == 404

        out = await api.post(url, headers=sk.headers("analyst"))
        assert out.status_code == 200, out.text
        assert out.json()["status"] == "dismissed"
        assert out.json()["pattern"] == {"steps": [{"kind": "query"}]}
        assert _near(out.json()["dismissed_until"], _now() + datetime.timedelta(days=90))
        dismissed = dict(sk.events(suggestion))["skill_suggestion.dismissed"]
        assert dismissed["status"] == "dismissed" and dismissed["dismissed_until"]

        again = await api.post(url, headers=sk.headers("analyst"))
        assert again.status_code == 409
        late = await api.post(
            f"/v1/skill-suggestions/{suggestion}:accept", headers=sk.headers("author")
        )
        assert late.status_code == 409
        listed = await api.get(
            "/v1/skill-suggestions?status=dismissed", headers=sk.headers("analyst")
        )
        assert [i["id"] for i in listed.json()["items"]] == [str(suggestion)]


async def test_an_org_wide_skill_is_retired_from_its_own_data_space_only(sk: Org) -> None:
    async with client(sk) as api:
        made = await _skill(api, sk, "toan-to-chuc", who="far", space=sk.other, scope="org")
        version = await api.post(
            f"/v1/skills/{made['id']}/versions",
            json={"params_schema": SCHEMA, "steps": STEPS},
            headers=sk.headers("far", sk.other),
        )
        sk.approve(version.json()["id"])
        # Visible (and runnable) from A, but A's approver does not retire B's skill.
        seen = await api.get(f"/v1/skills/{made['id']}", headers=sk.headers("steward"))
        assert seen.status_code == 200
        refused = await api.post(
            f"/v1/skill-versions/{version.json()['id']}:retire",
            json={"reason": "Không dùng ở Tín dụng"},
            headers=sk.headers("steward"),
        )
        assert refused.status_code == 403, refused.text
        status = "SELECT status FROM skill.skill_version WHERE id = %s"
        assert sk.one(status, version.json()["id"]) == "published"
