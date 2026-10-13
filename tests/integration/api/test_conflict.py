"""Conflicts between sources, their resolution, source authority and resolution rules (A2
"Xung đột", UC-07-04, UC-07-05) against PostgreSQL.

Each test builds its own organisation: the Data Space's steward (AC03), a data admin, an analyst
who only asks; three sources of the Data Space (Core level 1, DWH level 2, an upload at level 3),
a source of a second Data Space and one of a second organisation. Inbox, qa and governance are
not running: the tests assert the outbox event, not its effect.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

KIND = "du_no"


@dataclass
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    sources: dict[str, uuid.UUID]
    seed: Seed
    foreign_org: uuid.UUID = field(default_factory=uuid.uuid4)
    foreign_space: uuid.UUID = field(default_factory=uuid.uuid4)
    tokens: dict[str, uuid.UUID] = field(default_factory=dict)

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer kc-{who}-{self.org.hex[:8]}",
            "X-Data-Space": str(space or self.space),
        }

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM conflict.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            value = row[0] if row else None
            # Another suite may register psycopg2's uuid adapter: compare ids as text.
            return str(value) if isinstance(value, uuid.UUID) else value

    def execute(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def _user(
    s: Space, org: uuid.UUID, name: str, role: uuid.UUID, spaces: list[uuid.UUID]
) -> uuid.UUID:
    token = f"kc-{name}-{s.org.hex[:8]}"
    user = s.seed.row(
        "org.app_user",
        org_id=org,
        external_id=token,
        username=f"{name}-{org.hex[:6]}",
        display_name=name,
        status="active",
    )
    s.tokens[token] = org
    s.seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        s.seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


def _source(seed: Seed, org: uuid.UUID, space: uuid.UUID, name: str, level: str) -> uuid.UUID:
    return seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name=name,
        kind="postgresql",
        secret_ref=f"secret/tenants/{org}/{name}",
        authority_level=level,
        status="active",
    )


@pytest.fixture
def s(seed: Seed) -> Space:
    org = seed.org()

    def role(o: uuid.UUID, code: str, caps: list[str]) -> uuid.UUID:
        return seed.row("org.role", org_id=o, code=code, name=code, capabilities=caps)

    steward_role = role(org, "STEWARD", ["ask", "steward"])
    admin_role = role(org, "ADMIN_DATA", ["ask", "admin_data"])
    ask_role = role(org, "ASK", ["ask"])
    steward_only = role(org, "STEWARD_ONLY", ["steward"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-TT", name="TT", steward_id=keeper)
    out = Space(org, space, other, {}, {}, seed)
    out.users = {
        "steward": _user(out, org, "steward", steward_role, [space, other]),
        "admin": _user(out, org, "admin", admin_role, [space]),
        "analyst": _user(out, org, "analyst", ask_role, [space]),
        "noask": _user(out, org, "noask", steward_only, [space]),
    }
    out.sources = {
        "core": _source(seed, org, space, "Core", "level_1"),
        "dwh": _source(seed, org, space, "DWH", "level_2"),
        "upload": _source(seed, org, space, "Excel", "level_3"),
        "elsewhere": _source(seed, org, other, "Core TT", "level_1"),
    }
    foreign = seed.org()
    out.foreign_org = foreign
    fkeeper = seed.row(
        "org.app_user",
        org_id=foreign,
        external_id=f"fk-{foreign.hex[:6]}",
        username="fk",
        display_name="Keeper",
    )
    out.foreign_space = seed.row(
        "org.data_space", org_id=foreign, code="DS-X", name="X", steward_id=fkeeper
    )
    out.sources["foreign"] = _source(seed, foreign, out.foreign_space, "Core", "level_1")
    return out


class Tokens:
    def __init__(self, s: Space) -> None:
        self.s = s

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.s.tokens.get(token, self.s.org))}


def client(s: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(s))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _claim(
    s: Space,
    source: str | None,
    value: float,
    *,
    space: uuid.UUID | None = None,
    org: uuid.UUID | None = None,
) -> uuid.UUID:
    return s.seed.row(
        "conflict.claim",
        org_id=org or s.org,
        data_space_id=space or s.space,
        subject_key="Chi nhánh Hà Nội T9",
        attribute=KIND,
        value_num=value,
        unit="VND",
        source_kind="dataset",
        source_ref=uuid.uuid4(),
        source_id=s.sources[source] if source else None,
    )


def _conflict(
    s: Space,
    sources: list[str | None],
    *,
    material: bool = True,
    status: str = "open",
    space: uuid.UUID | None = None,
    org: uuid.UUID | None = None,
    kind: str = "value",
    created_at: str | None = None,
    hitl: uuid.UUID | None = None,
    answers: list[uuid.UUID] | None = None,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    org = org or s.org
    space = space or s.space
    claims = [
        _claim(s, src, 1_000_000 + i * 2_000, space=space, org=org) for i, src in enumerate(sources)
    ]
    values: dict[str, Any] = {
        "org_id": org,
        "data_space_id": space,
        "kind": kind,
        "detected_at_stage": "answer",
        "subject_key": "Chi nhánh Hà Nội T9",
        "attribute": KIND,
        "delta": 2000,
        "delta_pct": 0.002,
        "material": material,
        "status": status,
        "hitl_request_id": hitl,
    }
    if status == "resolved":
        values["outcome"] = "noted"
    if created_at:
        values["created_at"] = created_at
    conflict = s.seed.row("conflict.conflict", **values)
    if answers:
        s.execute(
            "UPDATE conflict.conflict SET affected_answer_ids = %s::uuid[] WHERE id = %s",
            [str(a) for a in answers],
            conflict,
        )
    for i, claim in enumerate(claims):
        s.seed.row(
            "conflict.conflict_claim",
            org_id=org,
            conflict_id=conflict,
            claim_id=claim,
            role="primary" if i == 0 else "competing",
        )
    return conflict, claims


def _authority(s: Space, ranking: list[str], *, status: str = "active") -> uuid.UUID:
    authority = uuid.uuid4()
    s.execute(
        "INSERT INTO conflict.source_authority (id, org_id, data_space_id, data_kind, ranking,"
        " status, approved_by, approved_at) VALUES (%s, %s, %s, %s, %s::uuid[], %s, %s, %s)",
        authority,
        s.org,
        s.space,
        KIND,
        [str(s.sources[r]) for r in ranking],
        status,
        s.users["steward"] if status == "active" else None,
        datetime.datetime.now(datetime.UTC) if status == "active" else None,
    )
    return authority


# --- conflicts ----------------------------------------------------------------------------------


async def test_list_conflicts_filters_and_isolation(s: Space) -> None:
    material, _ = _conflict(s, ["core", "dwh"])
    minor, _ = _conflict(s, ["core", "dwh"], material=False, kind="definition")
    done, _ = _conflict(s, ["core", "dwh"], status="resolved")
    elsewhere, _ = _conflict(s, ["elsewhere", "elsewhere"], space=s.other)
    async with client(s) as api:
        h = s.headers("analyst")

        async def ids(query: str = "") -> set[str]:
            r = await api.get(f"/v1/conflicts{query}", headers=h)
            assert r.status_code == 200, r.text
            return {c["id"] for c in r.json()["items"]}

        assert await ids() == {str(material), str(minor), str(done)}
        assert await ids("?status=open") == {str(material), str(minor)}
        assert await ids("?kind=definition") == {str(minor)}
        assert await ids("?material=false") == {str(minor)}
        first = await api.get("/v1/conflicts?page_size=2", headers=h)
        token = first.json()["next_page_token"]
        assert len(first.json()["items"]) == 2 and token
        rest = await api.get(f"/v1/conflicts?page_size=2&page_token={token}", headers=h)
        assert len(rest.json()["items"]) == 1

        there = await api.get("/v1/conflicts", headers=s.headers("steward", s.other))
        assert {c["id"] for c in there.json()["items"]} == {str(elsewhere)}
        hidden = await api.get(f"/v1/conflicts/{elsewhere}", headers=h)
        assert hidden.status_code == 404
        # Without ask nothing of the conflicts is read.
        no_ask = s.headers("noask")
        assert (await api.get("/v1/conflicts", headers=no_ask)).status_code == 403
        assert (await api.get(f"/v1/conflicts/{material}", headers=no_ask)).status_code == 403


async def test_a_conflict_of_another_organisation_is_not_found(s: Space) -> None:
    foreign, _ = _conflict(s, ["foreign", "foreign"], space=s.foreign_space, org=s.foreign_org)
    async with client(s) as api:
        got = await api.get(f"/v1/conflicts/{foreign}", headers=s.headers("analyst"))
        assert got.status_code == 404
        resolve = await api.post(
            f"/v1/conflicts/{foreign}:resolve",
            json={"outcome": "both_presented"},
            headers=s.headers("steward"),
        )
        assert resolve.status_code == 404
    assert s.one("SELECT status FROM conflict.conflict WHERE id = %s", foreign) == "open"


# AC-07-04-01
@pytest.mark.req("AC-07-04-01")
async def test_compare_two_sources_shows_claims_ranks_and_the_suggestion(s: Space) -> None:
    conflict, claims = _conflict(s, ["dwh", "core"], material=False)
    async with client(s) as api:
        h = s.headers("analyst")
        # No approved ranking yet: a person decides.
        bare = await api.get(f"/v1/conflicts/{conflict}", headers=h)
        assert bare.status_code == 200, bare.text
        assert bare.json()["suggestion"] == {
            "claim_id": None,
            "outcome": "resolved_by_human",
            "basis": "no_basis",
        }
        assert bare.json()["authority"] is None

        # A draft ranking is shown but not applied (UC-07-05 step 5).
        authority = _authority(s, ["core", "dwh"], status="draft")
        draft = await api.get(f"/v1/conflicts/{conflict}", headers=h)
        assert draft.json()["authority"]["id"] == str(authority)
        assert draft.json()["suggestion"]["basis"] == "no_basis"
        assert all(c["rank"] is None for c in draft.json()["claims"])

        # Approved: below the materiality threshold the higher source is used and noted.
        s.execute(
            "UPDATE conflict.source_authority SET status = 'active', approved_by = %s,"
            " approved_at = now() WHERE id = %s",
            s.users["steward"],
            authority,
        )
        got = await api.get(f"/v1/conflicts/{conflict}", headers=h)
    view = got.json()
    by_id = {c["id"]: c for c in view["claims"]}
    core_claim = by_id[str(claims[1])]
    assert core_claim["source"] == {
        "id": str(s.sources["core"]),
        "name": "Core",
        "kind": "postgresql",
        "authority_level": "level_1",
    }
    assert core_claim["rank"] == 1 and by_id[str(claims[0])]["rank"] == 2
    assert by_id[str(claims[0])]["role"] == "primary"
    assert view["suggestion"] == {
        "claim_id": str(claims[1]),
        "outcome": "noted",
        "basis": "source_authority",
    }
    # The source brief carries no connection detail or secret.
    assert "secret" not in got.text and "host" not in core_claim["source"]


# AC-07-04-05
@pytest.mark.req("AC-07-04-05")
async def test_an_approved_rule_beats_the_ranking(s: Space) -> None:
    conflict, claims = _conflict(s, ["core", "dwh"])
    _authority(s, ["core", "dwh"])
    async with client(s) as api:
        h = s.headers("analyst")
        ranked = await api.get(f"/v1/conflicts/{conflict}", headers=h)
        assert ranked.json()["suggestion"] == {
            "claim_id": str(claims[0]),
            "outcome": "auto_resolved",
            "basis": "source_authority",
        }
        rule = await api.post(
            "/v1/resolution-rules",
            json={
                "data_kind": KIND,
                "condition": {"kind": "value"},
                "action": {"source_id": str(s.sources["dwh"])},
            },
            headers=s.headers("steward"),
        )
        assert rule.status_code == 201, rule.text
        ruled = await api.get(f"/v1/conflicts/{conflict}", headers=h)
    assert ruled.json()["suggestion"] == {
        "claim_id": str(claims[1]),
        "outcome": "auto_resolved",
        "basis": "resolution_rule",
    }


# AC-07-05-02
@pytest.mark.req("AC-07-05-02")
async def test_two_claims_of_equal_rank_need_a_person(s: Space) -> None:
    conflict, _ = _conflict(s, ["dwh", "dwh"])
    _authority(s, ["core", "dwh"])
    async with client(s) as api:
        got = await api.get(f"/v1/conflicts/{conflict}", headers=s.headers("analyst"))
    assert got.json()["suggestion"]["basis"] == "no_basis"
    assert got.json()["suggestion"]["claim_id"] is None


# AC-07-04-02, AC-07-04-06
@pytest.mark.req("AC-07-04-02", "AC-07-04-06")
async def test_the_steward_chooses_a_source_and_remembers_it_as_a_rule(s: Space) -> None:
    hitl, answer = uuid.uuid4(), uuid.uuid4()
    conflict, claims = _conflict(s, ["core", "dwh"], hitl=hitl, answers=[answer])
    before = s.one("SELECT value_num FROM conflict.claim WHERE id = %s", claims[1])
    async with client(s) as api:
        url = f"/v1/conflicts/{conflict}:resolve"
        body = {
            "outcome": "resolved_by_human",
            "chosen_claim_id": str(claims[0]),
            "note": " Core là sổ cái ",
            "remember_as_rule": True,
        }
        # resolve needs steward: the data admin and the analyst do not have it.
        for who in ("admin", "analyst"):
            assert (await api.post(url, json=body, headers=s.headers(who))).status_code == 403
        resolved = await api.post(url, json=body, headers=s.headers("steward"))
        assert resolved.status_code == 200, resolved.text
        view = resolved.json()
        assert view["status"] == "resolved" and view["outcome"] == "resolved_by_human"
        assert view["chosen_claim_id"] == str(claims[0])
        assert view["decision_note"] == "Core là sổ cái"

        again = await api.post(url, json=body, headers=s.headers("steward"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"

    event = dict(s.events(conflict))["conflict.resolved"]
    assert event["conflict_id"] == str(conflict) and event["previous_status"] == "open"
    assert event["chosen_claim_id"] == str(claims[0])
    assert event["rejected_claim_ids"] == [str(claims[1])]
    # BR-31: the rejected source's owner is told and the answers that used it re-labelled.
    assert event["rejected_source_ids"] == [str(s.sources["dwh"])]
    assert event["affected_answer_ids"] == [str(answer)]
    assert event["hitl_request_id"] == str(hitl)
    assert event["resolved_by"] == str(s.users["steward"])

    rule = event["resolution_rule_id"]
    assert rule
    assert s.one("SELECT origin FROM conflict.resolution_rule WHERE id = %s", rule) == "hitl"
    assert s.one(
        "SELECT action->>'source_id' FROM conflict.resolution_rule WHERE id = %s", rule
    ) == str(s.sources["core"])
    assert s.one("SELECT data_kind FROM conflict.resolution_rule WHERE id = %s", rule) == KIND
    created = dict(s.events(rule))["resolution_rule.created"]
    assert created["origin"] == "hitl" and created["conflict_id"] == str(conflict)
    # BR-31: the system never changes the source figures to end a conflict.
    assert s.one("SELECT value_num FROM conflict.claim WHERE id = %s", claims[1]) == before


# AC-07-04-03
@pytest.mark.req("AC-07-04-03")
async def test_insufficient_basis_hands_it_to_the_engineer_and_stays_resolvable(s: Space) -> None:
    conflict, claims = _conflict(s, ["core", "dwh"])
    async with client(s) as api:
        url = f"/v1/conflicts/{conflict}:resolve"
        h = s.headers("steward")
        marked = await api.post(
            url, json={"outcome": "insufficient_basis", "note": "Chênh chưa giải thích"}, headers=h
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["status"] == "insufficient_basis"
        assert marked.json()["outcome"] is None
        assert [e for e, _ in s.events(conflict)] == ["conflict.marked_insufficient_basis"]

        presented = await api.post(url, json={"outcome": "both_presented"}, headers=h)
        assert presented.status_code == 200, presented.text
        assert presented.json()["status"] == "resolved"
        assert presented.json()["outcome"] == "both_presented"
    event = dict(s.events(conflict))["conflict.resolved"]
    assert event["previous_status"] == "insufficient_basis"
    assert event["rejected_claim_ids"] == [] and event["chosen_claim_id"] is None
    assert s.one("SELECT count(*) FROM conflict.resolution_rule WHERE org_id = %s", s.org) == 0
    assert len(claims) == 2


async def test_resolve_refusals(s: Space) -> None:
    conflict, claims = _conflict(s, ["core", None])
    other_conflict, other_claims = _conflict(s, ["core", "dwh"])
    elsewhere, _ = _conflict(s, ["elsewhere", "elsewhere"], space=s.other)
    async with client(s) as api:
        url = f"/v1/conflicts/{conflict}:resolve"
        h = s.headers("steward")
        stray = await api.post(
            url,
            json={"outcome": "resolved_by_human", "chosen_claim_id": str(other_claims[0])},
            headers=h,
        )
        assert stray.status_code == 422 and stray.json()["code"] == "validation_failed"
        no_choice = await api.post(url, json={"outcome": "resolved_by_human"}, headers=h)
        assert no_choice.status_code == 422
        choice_with_both = await api.post(
            url, json={"outcome": "both_presented", "chosen_claim_id": str(claims[0])}, headers=h
        )
        assert choice_with_both.status_code == 422
        rule_without_choice = await api.post(
            url, json={"outcome": "noted", "remember_as_rule": True}, headers=h
        )
        assert rule_without_choice.status_code == 422
        not_an_outcome = await api.post(url, json={"outcome": "auto_resolved"}, headers=h)
        assert not_an_outcome.status_code == 422
        # A claim without a source cannot become a rule: a rule picks a source.
        sourceless = await api.post(
            url,
            json={
                "outcome": "resolved_by_human",
                "chosen_claim_id": str(claims[1]),
                "remember_as_rule": True,
            },
            headers=h,
        )
        assert sourceless.status_code == 422
        assert sourceless.json()["code"] == "business_rule_violated"
        # Another Data Space's conflict is not found from this one.
        hidden = await api.post(
            f"/v1/conflicts/{elsewhere}:resolve", json={"outcome": "both_presented"}, headers=h
        )
        assert hidden.status_code == 404
    for c in (conflict, other_conflict, elsewhere):
        assert s.one("SELECT status FROM conflict.conflict WHERE id = %s", c) == "open"
        assert s.events(c) == []


# --- source authority ---------------------------------------------------------------------------


# AC-07-05-01
@pytest.mark.req("AC-07-05-01")
async def test_put_source_authority_drafts_it_with_a_90_day_backtest(s: Space) -> None:
    recent = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10)
    _conflict(s, ["core", "dwh"], created_at=recent.isoformat())
    _conflict(s, ["dwh", "core"], created_at=recent.isoformat())
    _conflict(s, ["dwh", "dwh"], created_at=recent.isoformat())
    # Older than 90 days: not in the backtest.
    _conflict(s, ["core", "dwh"], created_at="2025-01-01T00:00:00Z")
    async with client(s) as api:
        h = s.headers("steward")
        body = {
            "data_kind": f" {KIND} ",
            "ranking": [str(s.sources["core"]), str(s.sources["dwh"])],
            "materiality": {"amount_pct": "0.001", "count": 1},
        }
        put = await api.put("/v1/source-authorities", json=body, headers=h)
        assert put.status_code == 200, put.text
        view = put.json()
        assert view["status"] == "draft" and view["data_kind"] == KIND
        assert view["approved_by"] is None
        assert view["ranking"] == body["ranking"]
        # Stored as a JSON number, like the column default (BR-120).
        assert view["materiality"] == {"amount_pct": 0.001, "count": 1}
        assert view["backtest"] == {
            "window_days": 90,
            "conflicts": 3,
            "would_auto_resolve": 2,
            "needs_person": 1,
            "chosen_by_source": {str(s.sources["core"]): 2},
        }
        event = dict(s.events(view["id"]))["source_authority.drafted"]
        assert event["ranking"] == body["ranking"] and event["previous_status"] is None
        assert event["drafted_by"] == str(s.users["steward"])

        # Approved, then changed (UC-07-05): the change is a new draft beside the active ranking,
        # which keeps applying until the draft is approved.
        s.execute(
            "UPDATE conflict.source_authority SET status = 'active', approved_by = %s,"
            " approved_at = now() WHERE id = %s",
            s.users["steward"],
            view["id"],
        )
        changed = await api.put(
            "/v1/source-authorities",
            json={**body, "ranking": list(reversed(body["ranking"]))},
            headers=h,
        )
        assert changed.status_code == 200, changed.text
        draft = changed.json()
        assert draft["id"] != view["id"] and draft["status"] == "draft"
        assert draft["approved_by"] is None
        assert draft["materiality"] == {"amount_pct": 0.001, "count": 1}
        assert (
            s.one("SELECT status FROM conflict.source_authority WHERE id = %s", view["id"])
            == "active"
        )
        drafted = dict(s.events(draft["id"]))["source_authority.drafted"]
        assert drafted["active_id"] == view["id"]
        # A second edit updates the same draft.
        again = await api.put("/v1/source-authorities", json=body, headers=h)
        assert again.json()["id"] == draft["id"]
        # Approving the draft supersedes the active one (routines/conflict.sql).
        s.execute(
            "UPDATE conflict.source_authority SET status = 'active', approved_by = %s,"
            " approved_at = now() WHERE id = %s",
            s.users["steward"],
            draft["id"],
        )
        assert (
            s.one("SELECT status FROM conflict.source_authority WHERE id = %s", view["id"])
            == "superseded"
        )

        listed = await api.get("/v1/source-authorities", headers=s.headers("analyst"))
        assert listed.status_code == 200
        assert {a["id"] for a in listed.json()["items"]} == {view["id"], draft["id"]}
        filtered = await api.get(
            "/v1/source-authorities?data_kind=so_khach", headers=s.headers("analyst")
        )
        assert filtered.json()["items"] == []
        there = await api.get("/v1/source-authorities", headers=s.headers("steward", s.other))
        assert there.json()["items"] == []
        no_ask = await api.get("/v1/source-authorities", headers=s.headers("noask"))
        assert no_ask.status_code == 403


# AC-07-05-04
@pytest.mark.req("AC-07-05-04")
async def test_an_upload_never_ranks_above_level_1(s: Space) -> None:
    async with client(s) as api:
        h = s.headers("steward")
        refused = await api.put(
            "/v1/source-authorities",
            json={
                "data_kind": KIND,
                "ranking": [str(s.sources["upload"]), str(s.sources["core"])],
            },
            headers=h,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        below = await api.put(
            "/v1/source-authorities",
            json={
                "data_kind": KIND,
                "ranking": [str(s.sources["core"]), str(s.sources["upload"])],
            },
            headers=h,
        )
        assert below.status_code == 200, below.text


async def test_put_source_authority_refusals(s: Space) -> None:
    async with client(s) as api:
        h = s.headers("steward")
        url = "/v1/source-authorities"
        for source in ("elsewhere", "foreign"):
            # A source of another Data Space or organisation is not a source here.
            stray = await api.put(
                url,
                json={
                    "data_kind": KIND,
                    "ranking": [str(s.sources["core"]), str(s.sources[source])],
                },
                headers=h,
            )
            assert stray.status_code == 422 and stray.json()["code"] == "validation_failed"
        twice = await api.put(
            url,
            json={"data_kind": KIND, "ranking": [str(s.sources["core"])] * 2},
            headers=h,
        )
        assert twice.status_code == 422
        blank = await api.put(
            url, json={"data_kind": "  ", "ranking": [str(s.sources["core"])]}, headers=h
        )
        assert blank.status_code == 422
        empty = await api.put(url, json={"data_kind": KIND, "ranking": []}, headers=h)
        assert empty.status_code == 422
        for who in ("admin", "analyst"):
            denied = await api.put(
                url,
                json={"data_kind": KIND, "ranking": [str(s.sources["core"])]},
                headers=s.headers(who),
            )
            assert denied.status_code == 403
    assert s.one("SELECT count(*) FROM conflict.source_authority WHERE org_id = %s", s.org) == 0


# --- resolution rules ---------------------------------------------------------------------------


async def test_create_resolution_rule(s: Space) -> None:
    async with client(s) as api:
        h = s.headers("steward")
        made = await api.post(
            "/v1/resolution-rules",
            json={
                "data_kind": KIND,
                "condition": {"kind": "value", "material": False},
                "action": {"source_id": str(s.sources["core"])},
            },
            headers=h,
        )
        assert made.status_code == 201, made.text
        view = made.json()
        assert view["origin"] == "manual" and view["status"] == "active"
        assert view["condition"] == {"kind": "value", "material": False}
        assert view["action"] == {"source_id": str(s.sources["core"])}
        assert s.one(
            "SELECT created_by FROM conflict.resolution_rule WHERE id = %s", view["id"]
        ) == str(s.users["steward"])
        event = dict(s.events(view["id"]))["resolution_rule.created"]
        assert event["resolution_rule_id"] == view["id"] and event["origin"] == "manual"
        assert event["data_space_id"] == str(s.space)

        bad_bodies = [
            # A condition key the matcher does not understand would never apply (BR-30).
            {
                "condition": {"delta_pct_lt": 0.0005},
                "action": {"source_id": str(s.sources["core"])},
            },
            {"condition": {}, "action": {}},
            {"condition": {}, "action": {"source_id": "not-a-uuid"}},
            {"condition": {}, "action": {"source_id": str(s.sources["elsewhere"])}},
            {"condition": {}, "action": {"source_id": str(s.sources["foreign"])}},
        ]
        for extra in bad_bodies:
            refused = await api.post(
                "/v1/resolution-rules", json={"data_kind": KIND, **extra}, headers=h
            )
            assert refused.status_code == 422, (extra, refused.text)
            assert refused.json()["code"] == "validation_failed"
        blank = await api.post(
            "/v1/resolution-rules",
            json={
                "data_kind": " ",
                "condition": {},
                "action": {"source_id": str(s.sources["core"])},
            },
            headers=h,
        )
        assert blank.status_code == 422
        for who in ("admin", "analyst"):
            denied = await api.post(
                "/v1/resolution-rules",
                json={
                    "data_kind": KIND,
                    "condition": {},
                    "action": {"source_id": str(s.sources["core"])},
                },
                headers=s.headers(who),
            )
            assert denied.status_code == 403
    assert s.one("SELECT count(*) FROM conflict.resolution_rule WHERE org_id = %s", s.org) == 1


async def test_materiality_must_be_a_threshold(s: Space) -> None:
    async with client(s) as api:
        refused = await api.put(
            "/v1/source-authorities",
            json={
                "data_kind": KIND,
                "ranking": [str(s.sources["core"])],
                "materiality": {"amount_pct": 2},
            },
            headers=s.headers("steward"),
        )
    assert refused.status_code == 422
