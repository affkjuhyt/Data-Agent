"""Thu thập (P14, UC-14-01 to UC-14-06) against PostgreSQL: sources and their legal review in force
(BR-174), OpenBao references, runs and pages (BR-150), schema inference, test and approval (BR-151),
revert and drift (BR-152, BR-168), the review queue (BR-153), consolidation specs and runs (BR-154,
BR-156) and entity links (BR-169).

Crawling, extracting and consolidating are the crawler's and the agent runtime's work: the api writes
the job row or nothing but the request, and the event. Rows those workers write (pages, records,
drifts, links) are seeded as the admin. Each test builds its own organisation.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

NOW = datetime.datetime.now(datetime.UTC)
SHA = "ab" * 32
OTHER_SHA = "cd" * 32
FIELDS = [
    {"name": "bank", "type": "text", "required": True, "key": True, "locator": "td.bank"},
    {
        "name": "rate_12m",
        "type": "percent",
        "required": True,
        "locator": "td.r12",
        "value_constraints": {"min": 0, "max": 15, "unit": "%/năm"},
    },
]
CHECKLIST = {k: {"result": "pass"} for k in ("terms", "robots", "copyright", "personal_data")}


def _evidence(sha: str = SHA) -> dict[str, Any]:
    return {
        "kind": "terms",
        "object_key": "legal/bank-a/terms.html",
        "sha256": sha,
        "captured_at": NOW.isoformat(),
    }


def _params(params: tuple[object, ...]) -> list[object]:
    return [str(p) if isinstance(p, uuid.UUID) else p for p in params]


@dataclass
class World:
    seed: Seed
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    _n: int = 0

    # ------------------------------------------------------------ http

    def client(self, who: str, space: uuid.UUID | None = None) -> httpx.AsyncClient:
        app = create_app(Settings(), token_verifier=_Tokens(self.org))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://api",
            headers={
                "Authorization": f"Bearer kc-{who}",
                "X-Data-Space": str(space or self.space),
            },
        )

    # ------------------------------------------------------------ reads

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, _params(params))
            row = cur.fetchone()
            return row[0] if row else None

    def run_sql(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, _params(params))

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM acquire.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def event(self, aggregate_id: object, event_type: str) -> dict[str, Any]:
        found = [p for t, p in self.events(aggregate_id) if t == event_type]
        assert found, f"no {event_type} for {aggregate_id}: {self.events(aggregate_id)}"
        return found[-1]

    # ------------------------------------------------------------ seeds (the workers' rows)

    def _name(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix} {self._n}"

    def source(
        self,
        *,
        space: uuid.UUID | None = None,
        owner: str = "owner",
        legal: bool = True,
        active: bool = True,
        status: str | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        """A source (legal review in force by default) and, when active, its active version 1."""
        values: dict[str, Any] = {
            "org_id": self.org,
            "data_space_id": space or self.space,
            "name": self._name("Nguồn"),
            "goal": "Lãi suất huy động 12 tháng",
            "kind": "web_page",
            "seed_urls": Json(["https://bank.example.vn/lai-suat"]),
            "allowed_domains": ["bank.example.vn"],
            "trust_level": 0.9,
            "owner_id": self.users[owner],
            "terms_url": "https://bank.example.vn/dieu-khoan",
        }
        if legal:
            values.update(
                legal_status="approved",
                legal_checklist=Json(CHECKLIST),
                legal_evidence=Json([_evidence()]),
                legal_reviewed_by=self.users["legal"],
                legal_reviewed_at=NOW - datetime.timedelta(days=1),
                legal_review_due_at=NOW + datetime.timedelta(days=90),
                terms_sha256=SHA,
            )
        source = self.seed.row("acquire.crawl_source", **values)
        version = None
        if active:
            version = self.version(source, 1, status="active")
            self.run_sql(
                "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s"
                " WHERE id = %s",
                version,
                source,
            )
        if status is not None:
            self.run_sql(
                "UPDATE acquire.crawl_source SET status = %s WHERE id = %s", status, source
            )
        return source, version

    def version(
        self,
        source: uuid.UUID,
        number: int,
        *,
        status: str = "draft",
        accuracy: float | None = None,
    ) -> uuid.UUID:
        values: dict[str, Any] = {
            "org_id": self.org,
            "source_id": source,
            "version": number,
            "status": status,
            "record_selector": "table.lai-suat tr",
            "fields": Json(FIELDS),
        }
        if status in ("active", "superseded"):
            values.update(
                approved_by=self.users["owner"],
                approved_at=NOW - datetime.timedelta(days=10 - number),
                test_required_accuracy=accuracy or 0.97,
            )
        elif accuracy is not None:
            values["test_required_accuracy"] = accuracy
        return self.seed.row("acquire.crawl_schema_version", **values)

    def run(
        self,
        source: uuid.UUID,
        version: uuid.UUID | None,
        *,
        status: str = "in_review",
        sample_accuracy: float | None = None,
    ) -> uuid.UUID:
        return self.seed.row(
            "acquire.crawl_run",
            org_id=self.org,
            source_id=source,
            schema_version_id=version,
            trigger="schedule",
            status=status,
            sample_accuracy=sample_accuracy,
            started_at=NOW - datetime.timedelta(hours=1),
        )

    def page(
        self, run: uuid.UUID, source: uuid.UUID, *, retain_until: datetime.datetime | None = None
    ) -> uuid.UUID:
        return self.seed.row(
            "acquire.crawl_page",
            org_id=self.org,
            run_id=run,
            source_id=source,
            url="https://bank.example.vn/lai-suat",
            fetched_at=NOW,
            http_status=200,
            content_type="text/html",
            sha256=SHA,
            object_key=f"da-raw-crawl/{uuid.uuid4()}.html",
            byte_size=2048,
            retain_until=retain_until,
        )

    def record(
        self,
        run: uuid.UUID,
        page: uuid.UUID,
        version: uuid.UUID,
        *,
        status: str = "pending",
        reasons: list[str] | None = None,
        sample: bool = False,
    ) -> uuid.UUID:
        values: dict[str, Any] = {
            "org_id": self.org,
            "run_id": run,
            "page_id": page,
            "schema_version_id": version,
            "entity_key": "SBV:01201001",
            "period": "2026-10-12",
            "values": Json({"bank": "Ngân hàng A", "rate_12m": 4.8}),
            "field_confidence": Json({"bank": 0.99, "rate_12m": 0.8}),
            "record_confidence": 0.8,
            "status": status,
            "is_random_sample": sample,
        }
        if reasons:
            values["review_reasons"] = reasons
        return self.seed.row("acquire.extracted_record", **values)

    def drift(
        self,
        source: uuid.UUID,
        run: uuid.UUID,
        from_version: uuid.UUID,
        *,
        kind: str = "structural",
        severity: str = "major",
        proposed: uuid.UUID | None = None,
    ) -> uuid.UUID:
        action = (
            "quarantine"
            if kind == "semantic"
            else ("auto_remap" if severity == "minor" else "paused")
        )
        return self.seed.row(
            "acquire.schema_drift",
            org_id=self.org,
            source_id=source,
            run_id=run,
            from_version_id=from_version,
            drift_kind=kind,
            severity=severity,
            action=action,
            signals=Json({"affected_fields": ["rate_12m"], "evidence": {"median_ratio": 10}}),
            proposed_version_id=proposed,
        )

    def reference(
        self, sbv: str = "01201001", *, entity_type: str = "bank", status: str = "active"
    ) -> uuid.UUID:
        values: dict[str, Any] = {
            "org_id": self.org,
            "entity_type": entity_type,
            "canonical_key": f"SBV:{sbv}",
            "legal_name": f"Ngân hàng {sbv}",
            "sbv_code": sbv,
            "registry_source": "sbv_registry",
            "status": status,
        }
        if status == "merged":
            values["successor_id"] = self.reference(str(int(sbv) + 1).zfill(8))
        return self.seed.row("acquire.reference_entity", **values)

    def link(
        self,
        source: uuid.UUID,
        *,
        space: uuid.UUID | None = None,
        key: str = "LPBank",
        status: str = "pending",
        entity_type: str = "bank",
    ) -> uuid.UUID:
        return self.seed.row(
            "acquire.entity_link",
            org_id=self.org,
            data_space_id=space or self.space,
            entity_type=entity_type,
            canonical_key=f"chua-xac-dinh:{key}",
            source_id=source,
            source_key=key,
            match_score=0.82,
            method="fuzzy",
            status=status,
        )

    def dataset(self, *, origin: str = "consolidated") -> uuid.UUID:
        return self.seed.row(
            "catalog.dataset",
            org_id=self.org,
            data_space_id=self.space,
            origin=origin,
            schema_name="silver",
            table_name=f"lai_suat_{uuid.uuid4().hex[:8]}",
            history_kind="snapshot",
            enabled=True,
        )


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def _org(seed: Seed) -> World:
    org = seed.org()
    roles = {
        name: seed.row("org.role", org_id=org, code=name.upper(), name=name, capabilities=caps)
        for name, caps in (
            ("steward", ["ask", "steward"]),
            ("admin_data", ["ask", "admin_data"]),
            ("compliance", ["ask", "compliance"]),
            ("ask", ["ask"]),
        )
    }
    keeper = seed.row(
        "org.app_user", org_id=org, external_id="kc-keeper", username="keeper", display_name="K"
    )
    space = seed.row(
        "org.data_space", org_id=org, code="DS-THI-TRUONG", name="Thị trường", steward_id=keeper
    )
    other = seed.row("org.data_space", org_id=org, code="DS-KHAC", name="Khác", steward_id=keeper)
    world = World(seed, org, space, other)
    members = {
        "owner": (["steward"], [space, other]),
        "steward": (["steward"], [space, other]),
        "admin": (["admin_data"], [space]),
        "legal": (["compliance"], [space]),
        # A steward who is also compliance officer: may not review their own source (BR-174).
        "owner_legal": (["steward", "compliance"], [space]),
        "asker": (["ask"], [space]),
    }
    for name, (role_names, spaces) in members.items():
        user = seed.row(
            "org.app_user", org_id=org, external_id=f"kc-{name}", username=name, display_name=name
        )
        world.users[name] = user
        for role_name in role_names:
            if role_name == "compliance":
                # compliance is organisation-wide: a Data Space role does not grant it.
                seed.row(
                    "org.user_role",
                    org_id=org,
                    user_id=user,
                    role_id=roles[role_name],
                    source="manual",
                )
            for s in spaces:
                seed.row(
                    "org.data_space_member",
                    org_id=org,
                    data_space_id=s,
                    principal_type="user",
                    principal_id=user,
                    role_id=roles[role_name],
                )
    return world


@pytest.fixture
def w(seed: Seed) -> World:
    return _org(seed)


def _source_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Lãi suất ngân hàng A",
        "goal": "Lãi suất huy động 12 tháng của các ngân hàng hằng ngày",
        "kind": "web_page",
        "seed_urls": ["https://bank.example.vn/lai-suat"],
        "allowed_domains": ["bank.example.vn"],
        "max_depth": 3,
        "schedule_cron": "0 7 * * *",
        "rate_limit_rps": 1,
        "review_gate": "exceptions",
        "trust_level": 0.9,
        "terms_url": "https://bank.example.vn/dieu-khoan",
    }
    body.update(extra)
    return body


def _legal_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "legal_status": "approved",
        "terms_url": "https://bank.example.vn/dieu-khoan",
        "legal_checklist": CHECKLIST,
        "legal_evidence": [_evidence()],
    }
    body.update(extra)
    return body


# ================================================================ capability gate


@pytest.mark.security
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/crawl-sources"),
        ("POST", "/v1/crawl-sources"),
        ("GET", "/v1/crawl-runs"),
        ("GET", "/v1/schema-drifts"),
        ("POST", "/v1/extracted-records:bulk-review"),
        ("GET", "/v1/consolidation-specs"),
        ("GET", "/v1/entity-links"),
    ],
)
async def test_without_steward_or_admin_data_every_route_is_403(
    w: World, method: str, path: str
) -> None:
    async with w.client("asker") as c:
        r = await c.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 403, r.text


@pytest.mark.security
async def test_admin_data_is_the_other_capability_of_the_group(w: World) -> None:
    w.source()
    async with w.client("admin") as c:
        r = await c.get("/v1/crawl-sources")
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


# ================================================================ crawl sources (UC-14-01, UC-14-06)


@pytest.mark.req("AC-14-01-01", "AC-14-01-04")
async def test_create_source_is_a_draft_with_its_event(w: World) -> None:
    async with w.client("owner") as c:
        r = await c.post("/v1/crawl-sources", json=_source_body())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["legal_status"] == "pending"
    assert body["owner_id"] == str(w.users["owner"])
    assert body["review_gate"] == "exceptions"
    assert body["has_secret"] is False
    assert w.one("SELECT status FROM acquire.crawl_source WHERE id = %s", body["id"]) == "draft"
    event = w.event(body["id"], "acquire.source.created")
    assert event["source_id"] == body["id"]
    assert event["data_space_id"] == str(w.space)
    assert event["status"] == "draft"


async def test_create_source_refuses_urls_outside_its_domains(w: World) -> None:
    async with w.client("owner") as c:
        seeds = await c.post(
            "/v1/crawl-sources",
            json=_source_body(seed_urls=["https://evil.example.com/x"]),
        )
        terms = await c.post(
            "/v1/crawl-sources", json=_source_body(terms_url="https://other.example.org/tos")
        )
        login = await c.post(
            "/v1/crawl-sources",
            json=_source_body(seed_urls=["https://user:pw@bank.example.vn/lai-suat"]),
        )
    assert seeds.status_code == 422, seeds.text
    assert seeds.json()["errors"][0]["field"] == "seed_urls"
    assert terms.status_code == 422, terms.text
    assert login.status_code == 422, login.text


@pytest.mark.req("AC-14-01-03")
async def test_a_public_api_source_with_openapi_tells_the_agent_to_read_it(w: World) -> None:
    async with w.client("owner") as c:
        r = await c.post(
            "/v1/crawl-sources",
            json=_source_body(
                kind="public_api", openapi_url="https://bank.example.vn/openapi.json"
            ),
        )
        not_api = await c.post(
            "/v1/crawl-sources",
            json=_source_body(name="Trang", openapi_url="https://bank.example.vn/openapi.json"),
        )
    assert r.status_code == 201, r.text
    assert w.event(r.json()["id"], "acquire.source.created")["has_openapi"] is True
    assert not_api.status_code == 422, not_api.text


@pytest.mark.security
async def test_secret_ref_is_an_openbao_path_of_the_org_and_never_returned(w: World) -> None:
    other_org = uuid.uuid4()
    async with w.client("owner") as c:
        foreign = await c.post(
            "/v1/crawl-sources",
            json=_source_body(
                auth_mode="api_key", secret_ref=f"tenants/{other_org}/crawl-sources/bank-a"
            ),
        )
        climb = await c.post(
            "/v1/crawl-sources",
            json=_source_body(
                auth_mode="api_key", secret_ref=f"tenants/{w.org}/crawl-sources/../../{other_org}/k"
            ),
        )
        missing = await c.post("/v1/crawl-sources", json=_source_body(auth_mode="api_key"))
        # Another secret of the same organisation (a warehouse password) is not a source's key.
        elsewhere = await c.post(
            "/v1/crawl-sources",
            json=_source_body(auth_mode="api_key", secret_ref=f"tenants/{w.org}/sources/dw"),
        )
        made = await c.post(
            "/v1/crawl-sources",
            json=_source_body(
                auth_mode="api_key", secret_ref=f"tenants/{w.org}/crawl-sources/bank-a/api-key"
            ),
        )
        assert made.status_code == 201, made.text
        source = made.json()["id"]
        detail = await c.get(f"/v1/crawl-sources/{source}")
        listed = await c.get("/v1/crawl-sources")
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["errors"][0]["field"] == "secret_ref"
    assert climb.status_code == 422, climb.text
    assert missing.status_code == 422, missing.text
    assert elsewhere.status_code == 422, elsewhere.text
    assert made.json()["has_secret"] is True
    for text in (made.text, detail.text, listed.text, str(w.events(source))):
        assert "secret_ref" not in text
        assert "bank-a/api-key" not in text
    assert (
        w.one("SELECT secret_ref FROM acquire.crawl_source WHERE id = %s", source)
        == f"tenants/{w.org}/crawl-sources/bank-a/api-key"
    )


@pytest.mark.req("AC-14-06-01")
async def test_list_sources_shows_monitoring_columns_and_filters(w: World) -> None:
    healthy, version = w.source()
    held, held_version = w.source(status="paused")
    run = w.run(healthy, version, status="in_review", sample_accuracy=0.96)
    page = w.page(run, healthy)
    w.record(run, page, version, status="flagged", reasons=["outlier"])
    w.record(run, page, version, status="approved")
    w.source(space=w.other_space)
    async with w.client("owner") as c:
        everything = await c.get("/v1/crawl-sources")
        issues = await c.get("/v1/crawl-sources", params={"has_issue": "true"})
        active = await c.get("/v1/crawl-sources", params={"status": "active"})
    assert everything.status_code == 200, everything.text
    items = {i["id"]: i for i in everything.json()["items"]}
    # The other Data Space's source is not listed.
    assert set(items) == {str(healthy), str(held)}
    assert items[str(healthy)]["pending_review"] == 1
    assert items[str(healthy)]["last_run_status"] == "in_review"
    assert float(items[str(healthy)]["sample_accuracy"]) == pytest.approx(0.96)
    assert items[str(healthy)]["has_issue"] is False
    assert items[str(held)]["has_issue"] is True
    assert [i["id"] for i in issues.json()["items"]] == [str(held)]
    assert [i["id"] for i in active.json()["items"]] == [str(healthy)]
    assert held_version is not None


async def test_get_source_has_active_schema_and_recent_runs_and_is_scoped(
    w: World, seed: Seed
) -> None:
    source, version = w.source()
    runs = [w.run(source, version, status="published") for _ in range(6)]
    elsewhere, _ = w.source(space=w.other_space)
    stranger = _org(seed)
    foreign, _ = stranger.source()
    async with w.client("owner") as c:
        r = await c.get(f"/v1/crawl-sources/{source}")
        other_space = await c.get(f"/v1/crawl-sources/{elsewhere}")
        other_org = await c.get(f"/v1/crawl-sources/{foreign}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_schema"]["id"] == str(version)
    assert len(body["recent_runs"]) == 5
    assert {x["id"] for x in body["recent_runs"]} <= {str(x) for x in runs}
    assert other_space.status_code == 404
    assert other_org.status_code == 404


async def test_patch_source_emits_and_a_retired_source_is_not_edited(w: World) -> None:
    source, _ = w.source()
    retired, _ = w.source(active=False, status="retired")
    async with w.client("owner") as c:
        r = await c.patch(f"/v1/crawl-sources/{source}", json={"max_depth": 2, "name": " Mới "})
        gone = await c.patch(f"/v1/crawl-sources/{retired}", json={"max_depth": 2})
        nulled = await c.patch(f"/v1/crawl-sources/{source}", json={"goal": None})
    assert r.status_code == 200, r.text
    assert r.json()["max_depth"] == 2
    assert r.json()["name"] == "Mới"
    assert w.event(source, "acquire.source.updated")["changed"] == ["max_depth", "name"]
    assert gone.status_code == 409, gone.text
    assert nulled.status_code == 422, nulled.text


# ---------------------------------------------------------------- legal review (BR-174)


@pytest.mark.req("AC-14-01-02")
async def test_legal_review_approves_with_six_months_and_the_terms_hash(w: World) -> None:
    source, _ = w.source(legal=False, active=False)
    async with w.client("legal") as c:
        r = await c.post(f"/v1/crawl-sources/{source}:legal-review", json=_legal_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["legal_status"] == "approved"
    assert body["legal_reviewed_by"] == str(w.users["legal"])
    assert body["terms_sha256"] == SHA
    reviewed = datetime.datetime.fromisoformat(body["legal_reviewed_at"])
    due = datetime.datetime.fromisoformat(body["legal_review_due_at"])
    # Six calendar months (10/10/2026 → 10/04/2027).
    assert (due.year * 12 + due.month) - (reviewed.year * 12 + reviewed.month) == 6
    assert due.day == reviewed.day or due.day < reviewed.day
    event = w.event(source, "acquire.source.legal_reviewed")
    assert event["legal_status"] == "approved"
    assert event["legal_reviewed_by"] == str(w.users["legal"])
    assert (
        w.one("SELECT legal_status FROM acquire.crawl_source WHERE id = %s", source) == "approved"
    )


@pytest.mark.req("AC-14-01-08")
@pytest.mark.security
async def test_legal_review_refusals(w: World) -> None:
    own, _ = w.source(owner="owner_legal", legal=False, active=False)
    source, _ = w.source(legal=False, active=False)
    async with w.client("owner_legal") as c:
        self_review = await c.post(f"/v1/crawl-sources/{own}:legal-review", json=_legal_body())
    async with w.client("steward") as c:
        not_compliance = await c.post(
            f"/v1/crawl-sources/{source}:legal-review", json=_legal_body()
        )
    async with w.client("legal") as c:
        no_evidence = await c.post(
            f"/v1/crawl-sources/{source}:legal-review", json=_legal_body(legal_evidence=[])
        )
        too_long = await c.post(
            f"/v1/crawl-sources/{source}:legal-review",
            json=_legal_body(legal_review_due_at=(NOW + datetime.timedelta(days=200)).isoformat()),
        )
        failed_item = await c.post(
            f"/v1/crawl-sources/{source}:legal-review",
            json=_legal_body(legal_checklist={**CHECKLIST, "robots": {"result": "fail"}}),
        )
        off_domain = await c.post(
            f"/v1/crawl-sources/{source}:legal-review",
            json=_legal_body(terms_url="https://elsewhere.example.org/tos"),
        )
    assert self_review.status_code == 403, self_review.text
    assert self_review.json()["code"] == "policy_denied"
    assert not_compliance.status_code == 403, not_compliance.text
    for r in (no_evidence, too_long, failed_item, off_domain):
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "validation_failed"
    assert w.one("SELECT legal_status FROM acquire.crawl_source WHERE id = %s", source) == "pending"
    assert w.events(source) == []


async def test_a_retired_source_is_not_reviewed(w: World) -> None:
    retired, _ = w.source(legal=False, active=False, status="retired")
    async with w.client("legal") as c:
        r = await c.post(f"/v1/crawl-sources/{retired}:legal-review", json=_legal_body())
    assert r.status_code == 409, r.text


@pytest.mark.req("AC-14-01-07")
async def test_a_rejected_legal_review_pauses_a_running_source(w: World) -> None:
    source, _ = w.source()
    async with w.client("legal") as c:
        r = await c.post(
            f"/v1/crawl-sources/{source}:legal-review",
            json=_legal_body(legal_status="rejected", legal_evidence=[]),
        )
    assert r.status_code == 200, r.text
    assert r.json()["legal_status"] == "rejected"
    assert r.json()["status"] == "paused"
    changed = w.event(source, "acquire.source.status_changed")
    assert (changed["previous_status"], changed["status"]) == ("active", "paused")


# ---------------------------------------------------------------- pause, resume, run now


@pytest.mark.req("AC-14-06-02")
async def test_pause_resume_and_run_now(w: World) -> None:
    source, version = w.source()
    w.run_sql("UPDATE acquire.crawl_source SET consecutive_blocked = 2 WHERE id = %s", source)
    async with w.client("owner") as c:
        blank = await c.post(f"/v1/crawl-sources/{source}:pause", json={"reason": "  "})
        paused = await c.post(
            f"/v1/crawl-sources/{source}:pause", json={"reason": "Ngân hàng đổi giao diện"}
        )
        again = await c.post(f"/v1/crawl-sources/{source}:pause", json={"reason": "x"})
        held_run = await c.post(f"/v1/crawl-sources/{source}/runs")
        resumed = await c.post(f"/v1/crawl-sources/{source}:resume")
        twice = await c.post(f"/v1/crawl-sources/{source}:resume")
        run = await c.post(f"/v1/crawl-sources/{source}/runs")
        busy = await c.post(f"/v1/crawl-sources/{source}/runs")
    assert blank.status_code == 422, blank.text
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    events = w.events(source)
    pause_event = next(p for e, p in events if e == "acquire.source.status_changed")
    assert pause_event["status"] == "paused" and "reason" not in pause_event
    # The mandatory reason goes to the audit trail, not the domain event.
    audit = next(p for e, p in events if e == "audit.requested")
    assert audit["after"] == {"status": "paused", "reason": "Ngân hàng đổi giao diện"}
    assert again.status_code == 409, again.text
    assert held_run.status_code == 409, held_run.text
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"
    assert resumed.json()["consecutive_blocked"] == 0
    assert twice.status_code == 409, twice.text
    assert run.status_code == 202, run.text
    run_id = run.json()["id"]
    assert run.headers["Location"] == f"/v1/crawl-runs/{run_id}"
    assert run.json()["status"] == "queued"
    row = w.one(
        "SELECT trigger || '/' || status || '/' || schema_version_id FROM acquire.crawl_run"
        " WHERE id = %s",
        run_id,
    )
    assert row == f"manual/queued/{version}"
    queued = w.event(run_id, "acquire.run.queued")
    assert queued["source_id"] == str(source)
    assert queued["trigger"] == "manual"
    # One run in progress per source.
    assert busy.status_code == 409, busy.text


async def test_resume_needs_a_schema_no_major_drift_and_a_legal_review_in_force(w: World) -> None:
    draft, _ = w.source(active=False, status="paused")
    drifting, version = w.source(status="paused")
    w.drift(drifting, w.run(drifting, version, status="quarantined"), version)
    lapsed, _ = w.source(status="paused")
    w.run_sql(
        "UPDATE acquire.crawl_source SET legal_reviewed_at = now() - interval '100 days',"
        " legal_review_due_at = now() - interval '1 day' WHERE id = %s",
        lapsed,
    )
    async with w.client("owner") as c:
        no_schema = await c.post(f"/v1/crawl-sources/{draft}:resume")
        open_drift = await c.post(f"/v1/crawl-sources/{drifting}:resume")
        expired = await c.post(f"/v1/crawl-sources/{lapsed}:resume")
    assert no_schema.status_code == 409, no_schema.text
    assert open_drift.status_code == 409, open_drift.text
    assert expired.status_code == 422, expired.text
    assert expired.json()["code"] == "business_rule_violated"


async def test_run_now_is_scoped_to_the_data_space(w: World) -> None:
    elsewhere, _ = w.source(space=w.other_space)
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-sources/{elsewhere}/runs")
    assert r.status_code == 404, r.text


# ================================================================ runs and pages


async def test_list_runs_filters_and_stays_in_the_data_space(w: World) -> None:
    source, version = w.source()
    published = w.run(source, version, status="published")
    failed = w.run(source, None, status="failed")
    other, other_version = w.source(space=w.other_space)
    w.run(other, other_version, status="published")
    async with w.client("owner") as c:
        everything = await c.get("/v1/crawl-runs")
        by_status = await c.get("/v1/crawl-runs", params={"status": "failed"})
        by_source = await c.get("/v1/crawl-runs", params={"source_id": str(source)})
        future = await c.get(
            "/v1/crawl-runs", params={"from": (NOW + datetime.timedelta(days=1)).isoformat()}
        )
    assert everything.status_code == 200, everything.text
    assert {i["id"] for i in everything.json()["items"]} == {str(published), str(failed)}
    assert [i["id"] for i in by_status.json()["items"]] == [str(failed)]
    assert len(by_source.json()["items"]) == 2
    assert future.json()["items"] == []


@pytest.mark.req("AC-14-02-07")
async def test_run_detail_lists_its_pages_and_pages_are_append_only(w: World) -> None:
    source, version = w.source()
    run = w.run(source, version)
    pages = [w.page(run, source) for _ in range(3)]
    other, other_version = w.source(space=w.other_space)
    hidden = w.run(other, other_version)
    async with w.client("owner") as c:
        r = await c.get(f"/v1/crawl-runs/{run}")
        outside = await c.get(f"/v1/crawl-runs/{hidden}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {p["id"] for p in body["pages"]} == {str(p) for p in pages}
    assert all(p["sha256"] == SHA and p["changed"] is True for p in body["pages"])
    assert body["pages_truncated"] is False
    assert outside.status_code == 404
    # BR-150: the module's role may add pages, never change or delete one.
    for privilege in ("UPDATE", "DELETE"):
        assert (
            w.one("SELECT has_table_privilege('svc_acquire', 'acquire.crawl_page', %s)", privilege)
            is False
        )


# ================================================================ schema versions (UC-14-02, -03)


async def test_schema_inference_is_queued_for_the_agent(w: World) -> None:
    source, _ = w.source(active=False)
    async with w.client("owner") as c:
        r = await c.post(
            f"/v1/crawl-sources/{source}/schema-inference",
            json={"sample_urls": ["https://bank.example.vn/lai-suat?p=2"], "hints": "cột 12 tháng"},
        )
        no_body = await c.post(f"/v1/crawl-sources/{source}/schema-inference")
        outside = await c.post(
            f"/v1/crawl-sources/{source}/schema-inference",
            json={"sample_urls": ["https://evil.example.com/a"]},
        )
    assert r.status_code == 202, r.text
    agent_run = r.json()["id"]
    assert r.headers["Location"] == f"/v1/runs/{agent_run}"
    (event,) = (e for _, e in w.events(source) if e["agent_run_id"] == agent_run)
    assert event["hints"] == "cột 12 tháng"
    assert event["sample_urls"] == ["https://bank.example.vn/lai-suat?p=2"]
    assert {e["agent_run_id"] for t, e in w.events(source)} == {agent_run, no_body.json()["id"]}
    assert no_body.status_code == 202, no_body.text
    assert outside.status_code == 422, outside.text


@pytest.mark.req("AC-14-01-08")
async def test_schema_inference_needs_a_legal_review_in_force(w: World) -> None:
    pending, _ = w.source(legal=False, active=False)
    retired, _ = w.source(active=False, status="retired")
    async with w.client("owner") as c:
        no_review = await c.post(f"/v1/crawl-sources/{pending}/schema-inference")
        gone = await c.post(f"/v1/crawl-sources/{retired}/schema-inference")
    assert no_review.status_code == 422, no_review.text
    assert no_review.json()["code"] == "business_rule_violated"
    assert gone.status_code == 409, gone.text
    assert w.events(pending) == []


async def test_list_schemas_filters_and_is_scoped(w: World) -> None:
    source, active = w.source()
    draft = w.version(source, 2)
    other, _ = w.source(space=w.other_space)
    async with w.client("owner") as c:
        everything = await c.get(f"/v1/crawl-sources/{source}/schemas")
        drafts = await c.get(f"/v1/crawl-sources/{source}/schemas", params={"status": "draft"})
        outside = await c.get(f"/v1/crawl-sources/{other}/schemas")
    assert everything.status_code == 200, everything.text
    assert {i["id"] for i in everything.json()["items"]} == {str(active), str(draft)}
    assert [i["id"] for i in drafts.json()["items"]] == [str(draft)]
    assert drafts.json()["items"][0]["fields"] == FIELDS
    assert outside.status_code == 404


async def test_schema_test_is_queued_and_clears_the_previous_result(w: World) -> None:
    source, active = w.source()
    assert active is not None
    draft = w.version(source, 2, accuracy=0.97)
    run = w.run(source, active)
    pages = [w.page(run, source) for _ in range(2)]
    other, other_version = w.source(space=w.other_space)
    foreign_page = w.page(w.run(other, other_version), other)
    async with w.client("owner") as c:
        r = await c.post(
            f"/v1/crawl-schemas/{draft}:test", json={"sample_page_ids": [str(p) for p in pages]}
        )
        foreign = await c.post(
            f"/v1/crawl-schemas/{draft}:test", json={"sample_page_ids": [str(foreign_page)]}
        )
        approved = await c.post(f"/v1/crawl-schemas/{active}:test")
    assert r.status_code == 202, r.text
    assert r.headers["Location"] == f"/v1/runs/{r.json()['id']}"
    assert (
        w.one("SELECT status FROM acquire.crawl_schema_version WHERE id = %s", draft) == "testing"
    )
    assert (
        w.one(
            "SELECT test_required_accuracy FROM acquire.crawl_schema_version WHERE id = %s", draft
        )
        is None
    )
    event = w.event(draft, "acquire.schema.test_requested")
    assert event["agent_run_id"] == r.json()["id"]
    assert sorted(event["sample_page_ids"]) == sorted(str(p) for p in pages)
    assert foreign.status_code == 422, foreign.text
    assert approved.status_code == 409, approved.text


@pytest.mark.req("AC-14-02-01", "AC-14-02-06")
async def test_the_owner_approves_a_tested_schema_and_the_source_goes_active(w: World) -> None:
    source, _ = w.source(active=False)
    draft = w.version(source, 1, status="testing", accuracy=0.97)
    async with w.client("steward") as c:
        not_owner = await c.post(f"/v1/crawl-schemas/{draft}:approve", json={})
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-schemas/{draft}:approve", json={"note": "Đúng 97%"})
    assert not_owner.status_code == 403, not_owner.text
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert r.json()["approved_by"] == str(w.users["owner"])
    assert (
        w.one(
            "SELECT status || '/' || active_schema_version_id FROM acquire.crawl_source WHERE id = %s",
            source,
        )
        == f"active/{draft}"
    )
    approved = w.event(draft, "acquire.schema.approved")
    assert approved["superseded_version_id"] is None
    assert w.event(source, "acquire.source.status_changed")["status"] == "active"


async def test_approving_a_new_version_supersedes_the_active_one(w: World) -> None:
    source, v1 = w.source()
    v2 = w.version(source, 2, accuracy=0.95)
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-schemas/{v2}:approve")
    assert r.status_code == 200, r.text
    assert w.one("SELECT status FROM acquire.crawl_schema_version WHERE id = %s", v1) == (
        "superseded"
    )
    assert w.event(v2, "acquire.schema.approved")["superseded_version_id"] == str(v1)


async def test_a_source_without_legal_review_stays_draft_when_its_schema_is_approved(
    w: World,
) -> None:
    source, _ = w.source(legal=False, active=False)
    draft = w.version(source, 1, accuracy=0.99)
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-schemas/{draft}:approve")
    assert r.status_code == 200, r.text
    assert w.one("SELECT status FROM acquire.crawl_source WHERE id = %s", source) == "draft"


@pytest.mark.req("AC-14-02-04")
async def test_approval_needs_95_percent_and_a_test(w: World) -> None:
    source, _ = w.source(active=False)
    low = w.version(source, 1, accuracy=0.93)
    untested = w.version(source, 2)
    rejected = w.version(source, 3, status="rejected")
    async with w.client("owner") as c:
        r93 = await c.post(f"/v1/crawl-schemas/{low}:approve")
        r_none = await c.post(f"/v1/crawl-schemas/{untested}:approve")
        r_rejected = await c.post(f"/v1/crawl-schemas/{rejected}:approve")
    assert r93.status_code == 422, r93.text
    assert r93.json()["code"] == "business_rule_violated"
    assert r_none.status_code == 409, r_none.text
    assert r_rejected.status_code == 409, r_rejected.text
    assert w.one("SELECT status FROM acquire.crawl_schema_version WHERE id = %s", low) == "draft"


async def test_schema_routes_are_scoped_to_the_data_space(w: World) -> None:
    other, _ = w.source(space=w.other_space, active=False)
    draft = w.version(other, 1, accuracy=0.99)
    async with w.client("owner") as c:
        approve = await c.post(f"/v1/crawl-schemas/{draft}:approve")
        test = await c.post(f"/v1/crawl-schemas/{draft}:test")
        revert = await c.post(f"/v1/crawl-schemas/{draft}:revert", json={"reason": "x"})
    assert (approve.status_code, test.status_code, revert.status_code) == (404, 404, 404)


@pytest.mark.req("AC-14-03-03", "AC-14-03-08")
async def test_revert_returns_to_the_last_good_version(w: World) -> None:
    source, v1 = w.source()
    assert v1 is not None
    v2 = w.version(source, 2, accuracy=0.96)
    # v2 replaces v1, then breaks.
    w.run_sql("UPDATE acquire.crawl_schema_version SET status = 'superseded' WHERE id = %s", v1)
    w.run_sql(
        "UPDATE acquire.crawl_schema_version SET status = 'active', approved_by = %s,"
        " approved_at = now() WHERE id = %s",
        w.users["owner"],
        v2,
    )
    w.run_sql(
        "UPDATE acquire.crawl_source SET active_schema_version_id = %s, status = 'paused'"
        " WHERE id = %s",
        v2,
        source,
    )
    published = w.run(source, v2, status="published")
    held = w.run(source, v2, status="quarantined")
    kept = w.page(published, source)
    expired = w.page(held, source, retain_until=NOW - datetime.timedelta(days=1))
    good = w.record(published, kept, v2, status="approved")
    bad = w.record(held, expired, v2, status="pending")
    older = w.record(w.run(source, v1, status="published"), kept, v1, status="approved")
    drift = w.drift(source, held, v2)
    async with w.client("owner") as c:
        not_active = await c.post(f"/v1/crawl-schemas/{v1}:revert", json={"reason": "x"})
        r = await c.post(f"/v1/crawl-schemas/{v2}:revert", json={"reason": "Mất cột kỳ hạn"})
    assert not_active.status_code == 409, not_active.text
    assert r.status_code == 202, r.text
    body = r.json()
    assert r.headers["Location"] == f"/v1/crawl-runs/{body['id']}"
    assert body["restored_version_id"] == str(v1)
    assert body["superseded_records"] == 2
    assert body["expired_page_ids"] == [str(expired)]
    status = "SELECT status FROM acquire.crawl_schema_version WHERE id = %s"
    assert (w.one(status, v1), w.one(status, v2)) == ("active", "rejected")
    record_status = "SELECT status FROM acquire.extracted_record WHERE id = %s"
    assert w.one(record_status, good) == "superseded"
    assert w.one(record_status, bad) == "superseded"
    assert w.one(record_status, older) == "approved"
    assert (
        w.one(
            "SELECT status || '/' || active_schema_version_id FROM acquire.crawl_source WHERE id = %s",
            source,
        )
        == f"active/{v1}"
    )
    assert (
        w.one(
            "SELECT trigger || '/' || status || '/' || schema_version_id FROM acquire.crawl_run"
            " WHERE id = %s",
            body["id"],
        )
        == f"retry/queued/{v1}"
    )
    assert w.one("SELECT resolution FROM acquire.schema_drift WHERE id = %s", drift) == "reverted"
    reverted = w.event(v2, "acquire.schema.reverted")
    assert reverted["republish_run_ids"] == [str(published)]
    assert sorted(reverted["affected_run_ids"]) == sorted([str(published), str(held)])
    assert w.event(body["id"], "acquire.run.queued")["trigger"] == "retry"
    assert w.event(drift, "acquire.schema.drift_resolved")["resolution"] == "reverted"


async def test_revert_needs_an_earlier_approved_version(w: World) -> None:
    _, only = w.source()
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-schemas/{only}:revert", json={"reason": "x"})
        blank = await c.post(f"/v1/crawl-schemas/{only}:revert", json={"reason": " "})
    assert r.status_code == 409, r.text
    assert blank.status_code == 422, blank.text


# ================================================================ schema drift (UC-14-03)


async def test_list_drifts_filters_and_embeds_the_proposed_version(w: World) -> None:
    source, v1 = w.source()
    assert v1 is not None
    run = w.run(source, v1, status="quarantined")
    proposal = w.version(source, 2, accuracy=0.97)
    open_one = w.drift(source, run, v1, proposed=proposal)
    semantic = w.drift(source, run, v1, kind="semantic")
    w.run_sql(
        "UPDATE acquire.schema_drift SET resolution = 'manual_fix', resolved_at = now(),"
        " resolved_by = %s WHERE id = %s",
        w.users["owner"],
        semantic,
    )
    other, other_v = w.source(space=w.other_space)
    assert other_v is not None
    w.drift(other, w.run(other, other_v), other_v)
    async with w.client("owner") as c:
        everything = await c.get("/v1/schema-drifts")
        still_open = await c.get("/v1/schema-drifts", params={"resolution": "open"})
        semantic_only = await c.get("/v1/schema-drifts", params={"drift_kind": "semantic"})
    assert everything.status_code == 200, everything.text
    assert {i["id"] for i in everything.json()["items"]} == {str(open_one), str(semantic)}
    (item,) = still_open.json()["items"]
    assert item["id"] == str(open_one)
    assert item["proposed_version"]["id"] == str(proposal)
    assert [i["id"] for i in semantic_only.json()["items"]] == [str(semantic)]


async def test_resolve_drift_refusals(w: World) -> None:
    source, v1 = w.source(status="paused")
    assert v1 is not None
    run = w.run(source, v1, status="quarantined")
    structural = w.drift(source, run, v1)
    draft = w.version(source, 2, accuracy=0.97)
    other, other_v = w.source(space=w.other_space)
    assert other_v is not None
    hidden = w.drift(other, w.run(other, other_v), other_v)
    async with w.client("owner") as c:
        false_alarm = await c.post(
            f"/v1/schema-drifts/{structural}:resolve", json={"resolution": "false_alarm"}
        )
        unapproved = await c.post(
            f"/v1/schema-drifts/{structural}:resolve",
            json={"resolution": "approved_new", "proposed_version_id": str(draft)},
        )
        still_active = await c.post(
            f"/v1/schema-drifts/{structural}:resolve", json={"resolution": "reverted"}
        )
        outside = await c.post(
            f"/v1/schema-drifts/{hidden}:resolve", json={"resolution": "manual_fix"}
        )
        fixed = await c.post(
            f"/v1/schema-drifts/{structural}:resolve", json={"resolution": "manual_fix"}
        )
        again = await c.post(
            f"/v1/schema-drifts/{structural}:resolve", json={"resolution": "manual_fix"}
        )
    assert false_alarm.status_code == 422, false_alarm.text
    assert false_alarm.json()["code"] == "business_rule_violated"
    assert unapproved.status_code == 422, unapproved.text
    assert still_active.status_code == 409, still_active.text
    assert outside.status_code == 404, outside.text
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["resolution"] == "manual_fix"
    assert fixed.json()["resolved_by"] == str(w.users["owner"])
    # Nothing else holds the source: it runs again (UC-14-03).
    assert w.one("SELECT status FROM acquire.crawl_source WHERE id = %s", source) == "active"
    assert w.event(structural, "acquire.schema.drift_resolved")["resolution"] == "manual_fix"
    assert again.status_code == 409, again.text


@pytest.mark.req("AC-14-03-07")
async def test_a_semantic_false_alarm_publishes_the_quarantined_run(w: World) -> None:
    source, v1 = w.source(status="quarantined")
    assert v1 is not None
    run = w.run(source, v1, status="quarantined")
    w.record(run, w.page(run, source), v1, status="approved", reasons=["semantic_shift"])
    drift = w.drift(source, run, v1, kind="semantic")
    async with w.client("owner") as c:
        blocked = await c.post(f"/v1/crawl-runs/{run}:publish")
        r = await c.post(f"/v1/schema-drifts/{drift}:resolve", json={"resolution": "false_alarm"})
    # BR-168: nothing of the source is published while the semantic drift is open.
    assert blocked.status_code == 422, blocked.text
    assert r.status_code == 200, r.text
    assert r.json()["resolution"] == "false_alarm"
    assert w.one("SELECT status FROM acquire.crawl_run WHERE id = %s", run) == "published"
    assert w.one("SELECT status FROM acquire.crawl_source WHERE id = %s", source) == "active"
    assert w.event(run, "acquire.run.published")["previous_status"] == "quarantined"
    resolved = w.event(drift, "acquire.schema.drift_resolved")
    assert resolved["quarantined_run_ids"] == [str(run)]
    assert resolved["reextract"] is False


@pytest.mark.req("AC-14-03-07")
async def test_a_semantic_drift_approved_new_reextracts_the_quarantined_runs(w: World) -> None:
    source, v1 = w.source(status="quarantined")
    assert v1 is not None
    run = w.run(source, v1, status="quarantined")
    v2 = w.version(source, 2, accuracy=0.98)
    drift = w.drift(source, run, v1, kind="semantic", proposed=v2)
    async with w.client("owner") as c:
        approved = await c.post(f"/v1/crawl-schemas/{v2}:approve")
        r = await c.post(f"/v1/schema-drifts/{drift}:resolve", json={"resolution": "approved_new"})
    assert approved.status_code == 200, approved.text
    assert r.status_code == 200, r.text
    assert r.json()["proposed_version"]["id"] == str(v2)
    assert w.one("SELECT status FROM acquire.crawl_run WHERE id = %s", run) == "quarantined"
    resolved = w.event(drift, "acquire.schema.drift_resolved")
    assert resolved["reextract"] is True
    assert resolved["quarantined_run_ids"] == [str(run)]
    assert resolved["proposed_version_id"] == str(v2)


# ================================================================ review queue (UC-14-04)


async def test_run_records_carry_their_origin_and_filter_by_reason(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    page = w.page(run, source)
    outlier = w.record(run, page, version, status="flagged", reasons=["outlier"])
    sampled = w.record(run, page, version, reasons=["random_sample"], sample=True)
    plain = w.record(run, page, version, status="approved")
    other, other_v = w.source(space=w.other_space)
    hidden = w.run(other, other_v)
    async with w.client("owner") as c:
        everything = await c.get(f"/v1/crawl-runs/{run}/records")
        by_reason = await c.get(
            f"/v1/crawl-runs/{run}/records", params={"review_reasons": "outlier,low_confidence"}
        )
        repeated = await c.get(
            f"/v1/crawl-runs/{run}/records",
            params=[("review_reasons", "random_sample"), ("review_reasons", "outlier")],
        )
        samples = await c.get(f"/v1/crawl-runs/{run}/records", params={"is_random_sample": "true"})
        flagged = await c.get(f"/v1/crawl-runs/{run}/records", params={"status": "flagged"})
        outside = await c.get(f"/v1/crawl-runs/{hidden}/records")
    assert everything.status_code == 200, everything.text
    items = {i["id"]: i for i in everything.json()["items"]}
    assert set(items) == {str(outlier), str(sampled), str(plain)}
    item = items[str(outlier)]
    assert item["source_page"]["page_id"] == str(page)
    assert item["source_page"]["sha256"] == SHA
    assert item["source_page"]["object_key"].startswith("da-raw-crawl/")
    assert item["locators"] == {"bank": "td.bank", "rate_12m": "td.r12"}
    assert by_reason.status_code == 200, by_reason.text
    assert [i["id"] for i in by_reason.json()["items"]] == [str(outlier)]
    assert {i["id"] for i in repeated.json()["items"]} == {str(outlier), str(sampled)}
    assert [i["id"] for i in samples.json()["items"]] == [str(sampled)]
    assert [i["id"] for i in flagged.json()["items"]] == [str(outlier)]
    assert outside.status_code == 404


@pytest.mark.req("AC-14-04-01")
async def test_review_confirm_correct_reject(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    page = w.page(run, source)
    a, b, c_, d = (
        w.record(run, page, version, status="flagged", reasons=["outlier"]) for _ in range(4)
    )
    async with w.client("steward") as c:
        confirm = await c.post(f"/v1/extracted-records/{a}:review", json={"decision": "confirm"})
        correct = await c.post(
            f"/v1/extracted-records/{b}:review",
            json={"decision": "correct", "corrected_values": {"rate_12m": 4.85}},
        )
        reject = await c.post(
            f"/v1/extracted-records/{c_}:review",
            json={"decision": "reject", "reason": "Trùng dòng"},
        )
        no_reason = await c.post(f"/v1/extracted-records/{d}:review", json={"decision": "reject"})
        no_values = await c.post(f"/v1/extracted-records/{d}:review", json={"decision": "correct"})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["record"]["status"] == "approved"
    assert correct.json()["record"]["status"] == "corrected"
    # BR-153: what a person corrected is kept as an example; the extraction itself is unchanged.
    assert correct.json()["decision"]["used_as_example"] is True
    assert correct.json()["record"]["values"]["rate_12m"] == 4.8
    assert correct.json()["decision"]["corrected_values"] == {"rate_12m": 4.85}
    assert reject.json()["record"]["status"] == "rejected"
    assert reject.json()["decision"]["decided_by"] == str(w.users["steward"])
    assert no_reason.status_code == 422, no_reason.text
    assert no_values.status_code == 422, no_values.text
    assert w.one("SELECT count(*) FROM acquire.review_decision WHERE record_id = %s", b) == 1
    reviewed = w.event(b, "acquire.record.reviewed")
    assert (reviewed["decision"], reviewed["status"], reviewed["run_id"]) == (
        "correct",
        "corrected",
        str(run),
    )


async def test_review_refuses_final_records_and_other_spaces(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    page = w.page(run, source)
    superseded = w.record(run, page, version, status="superseded")
    closed_run = w.run(source, version, status="published")
    final = w.record(closed_run, w.page(closed_run, source), version, status="approved")
    other, other_v = w.source(space=w.other_space)
    assert other_v is not None
    other_run = w.run(other, other_v)
    hidden = w.record(other_run, w.page(other_run, other), other_v)
    async with w.client("owner") as c:
        r1 = await c.post(
            f"/v1/extracted-records/{superseded}:review", json={"decision": "confirm"}
        )
        r2 = await c.post(f"/v1/extracted-records/{final}:review", json={"decision": "confirm"})
        r3 = await c.post(f"/v1/extracted-records/{hidden}:review", json={"decision": "confirm"})
    assert (r1.status_code, r2.status_code, r3.status_code) == (409, 409, 404)


@pytest.mark.req("AC-14-04-05")
async def test_bulk_review_by_filter_and_by_ids(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    page = w.page(run, source)
    same = [
        w.record(run, page, version, status="flagged", reasons=["low_confidence"])
        for _ in range(15)
    ]
    other_reason = w.record(run, page, version, status="flagged", reasons=["outlier"])
    async with w.client("owner") as c:
        r = await c.post(
            "/v1/extracted-records:bulk-review",
            json={
                "filter": {"run_id": str(run), "review_reasons": ["low_confidence"]},
                "decision": "confirm",
            },
        )
        by_ids = await c.post(
            "/v1/extracted-records:bulk-review",
            json={"record_ids": [str(other_reason)], "decision": "reject", "reason": "Sai"},
        )
        both = await c.post(
            "/v1/extracted-records:bulk-review",
            json={
                "record_ids": [str(other_reason)],
                "filter": {"run_id": str(run)},
                "decision": "confirm",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 15, "by_status": {"approved": 15}}
    assert (
        w.one(
            "SELECT count(*) FROM acquire.extracted_record WHERE run_id = %s AND status = 'approved'",
            run,
        )
        == 15
    )
    assert (
        w.one(
            "SELECT count(*) FROM acquire.review_decision WHERE record_id = ANY(%s::uuid[])",
            [str(x) for x in same],
        )
        == 15
    )
    event = next(p for t, p in w.events(run) if t == "acquire.records.bulk_reviewed")
    assert sorted(event["record_ids"]) == sorted(str(x) for x in same)
    assert by_ids.status_code == 200, by_ids.text
    assert by_ids.json() == {"updated": 1, "by_status": {"rejected": 1}}
    assert both.status_code == 422, both.text


async def test_bulk_review_refusals(w: World) -> None:
    source, version = w.source()
    assert version is not None
    closed = w.run(source, version, status="published")
    final = w.record(closed, w.page(closed, source), version, status="approved")
    other, other_v = w.source(space=w.other_space)
    assert other_v is not None
    other_run = w.run(other, other_v)
    hidden = w.record(
        other_run, w.page(other_run, other), other_v, status="flagged", reasons=["outlier"]
    )
    async with w.client("owner") as c:
        on_closed = await c.post(
            "/v1/extracted-records:bulk-review",
            json={"filter": {"run_id": str(closed)}, "decision": "confirm"},
        )
        ids_closed = await c.post(
            "/v1/extracted-records:bulk-review",
            json={"record_ids": [str(final)], "decision": "confirm"},
        )
        outside = await c.post(
            "/v1/extracted-records:bulk-review",
            json={"record_ids": [str(hidden)], "decision": "confirm"},
        )
        outside_run = await c.post(
            "/v1/extracted-records:bulk-review",
            json={"filter": {"run_id": str(other_run)}, "decision": "confirm"},
        )
    assert on_closed.status_code == 409, on_closed.text
    assert ids_closed.status_code == 409, ids_closed.text
    assert outside.status_code == 404, outside.text
    assert outside_run.status_code == 404, outside_run.text
    assert w.one("SELECT status FROM acquire.extracted_record WHERE id = %s", hidden) == "flagged"


@pytest.mark.req("AC-14-04-09", "AC-14-04-01")
async def test_publish_waits_for_the_queue(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    flagged = w.record(run, w.page(run, source), version, status="flagged", reasons=["outlier"])
    queued = w.run(source, version, status="queued")
    async with w.client("owner") as c:
        blocked = await c.post(f"/v1/crawl-runs/{run}:publish")
        await c.post(f"/v1/extracted-records/{flagged}:review", json={"decision": "confirm"})
        r = await c.post(f"/v1/crawl-runs/{run}:publish")
        twice = await c.post(f"/v1/crawl-runs/{run}:publish")
        not_ready = await c.post(f"/v1/crawl-runs/{queued}:publish")
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["code"] == "business_rule_violated"
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"
    assert r.json()["finished_at"] is not None
    published = w.event(run, "acquire.run.published")
    assert (published["source_id"], published["previous_status"]) == (str(source), "in_review")
    assert twice.status_code == 409, twice.text
    assert not_ready.status_code == 409, not_ready.text


@pytest.mark.req("AC-14-04-04")
async def test_a_relation_warning_does_not_block_publishing(w: World) -> None:
    source, version = w.source()
    assert version is not None
    run = w.run(source, version)
    page = w.page(run, source)
    warned = w.record(run, page, version, status="approved", reasons=["relation_warning"])
    async with w.client("owner") as c:
        listed = await c.get(
            f"/v1/crawl-runs/{run}/records", params={"review_reasons": "relation_warning"}
        )
        r = await c.post(f"/v1/crawl-runs/{run}:publish")
    assert [i["id"] for i in listed.json()["items"]] == [str(warned)]
    assert r.status_code == 200, r.text


@pytest.mark.req("AC-14-04-08")
async def test_publish_refuses_a_poor_random_sample(w: World) -> None:
    source, version = w.source()
    run = w.run(source, version, sample_accuracy=0.88)
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-runs/{run}:publish")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "business_rule_violated"
    assert w.one("SELECT status FROM acquire.crawl_run WHERE id = %s", run) == "in_review"


async def test_a_run_quarantined_by_a_major_drift_is_not_published_around_it(w: World) -> None:
    source, version = w.source(status="paused")
    assert version is not None
    run = w.run(source, version, status="quarantined")
    drift = w.drift(source, run, version)
    async with w.client("owner") as c:
        held = await c.post(f"/v1/crawl-runs/{run}:publish")
        await c.post(f"/v1/schema-drifts/{drift}:resolve", json={"resolution": "manual_fix"})
        released = await c.post(f"/v1/crawl-runs/{run}:publish")
    assert held.status_code == 422, held.text
    assert released.status_code == 200, released.text


async def test_publish_is_scoped_to_the_data_space(w: World) -> None:
    other, other_v = w.source(space=w.other_space)
    run = w.run(other, other_v)
    async with w.client("owner") as c:
        r = await c.post(f"/v1/crawl-runs/{run}:publish")
    assert r.status_code == 404, r.text


# ================================================================ consolidation (UC-14-05)


def _spec_body(*sources: uuid.UUID, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Lãi suất huy động bình quân",
        "entity_type": "bank",
        "grain": "bank, term, day",
        "source_ids": [str(s) for s in sources],
        "source_priority": [str(s) for s in sources],
        "aggregates": [{"field": "rate_12m", "fn": "avg"}],
    }
    body.update(extra)
    return body


async def test_create_spec(w: World) -> None:
    source, _ = w.source()
    other, _ = w.source(space=w.other_space)
    derived = w.dataset(origin="derived")
    async with w.client("owner") as c:
        r = await c.post("/v1/consolidation-specs", json=_spec_body(source, source))
        foreign = await c.post("/v1/consolidation-specs", json=_spec_body(other, name="Khác"))
        wrong_target = await c.post(
            "/v1/consolidation-specs",
            json=_spec_body(source, name="Đích sai", target_dataset_id=str(derived)),
        )
        empty = await c.post("/v1/consolidation-specs", json=_spec_body(name="Rỗng"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["source_ids"] == [str(source)]
    assert body["owner_id"] == body["last_edited_by"] == str(w.users["owner"])
    created = w.event(body["id"], "acquire.spec.created")
    assert created["source_ids"] == [str(source)]
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["errors"][0]["field"] == "source_ids"
    assert wrong_target.status_code == 422, wrong_target.text
    assert empty.status_code == 422, empty.text


async def test_list_specs_filters_and_is_scoped(w: World) -> None:
    source, _ = w.source()
    other, _ = w.source(space=w.other_space)
    async with w.client("owner") as c:
        bank = (await c.post("/v1/consolidation-specs", json=_spec_body(source))).json()["id"]
        corp = (
            await c.post(
                "/v1/consolidation-specs",
                json=_spec_body(source, name="DN", entity_type="enterprise"),
            )
        ).json()["id"]
    async with w.client("owner", w.other_space) as c:
        hidden = await c.post("/v1/consolidation-specs", json=_spec_body(other))
        assert hidden.status_code == 201, hidden.text
    async with w.client("owner") as c:
        everything = await c.get("/v1/consolidation-specs")
        banks = await c.get("/v1/consolidation-specs", params={"entity_type": "bank"})
        drafts = await c.get("/v1/consolidation-specs", params={"status": "active"})
    assert {i["id"] for i in everything.json()["items"]} == {bank, corp}
    assert [i["id"] for i in banks.json()["items"]] == [bank]
    assert drafts.json()["items"] == []


@pytest.mark.req("AC-14-05-04")
async def test_patch_spec_moves_last_editor_only_on_content(w: World) -> None:
    source, _ = w.source()
    second, _ = w.source()
    target = w.dataset()
    async with w.client("owner") as c:
        spec = (await c.post("/v1/consolidation-specs", json=_spec_body(source))).json()["id"]
    async with w.client("steward") as c:
        content = await c.patch(
            f"/v1/consolidation-specs/{spec}",
            json={"source_ids": [str(source), str(second)], "source_priority": [str(second)]},
        )
    async with w.client("owner") as c:
        rename = await c.patch(f"/v1/consolidation-specs/{spec}", json={"name": "Tên mới"})
        no_target = await c.patch(f"/v1/consolidation-specs/{spec}", json={"status": "active"})
        activate = await c.patch(
            f"/v1/consolidation-specs/{spec}",
            json={"status": "active", "target_dataset_id": str(target)},
        )
        nulled = await c.patch(f"/v1/consolidation-specs/{spec}", json={"grain": None})
        retire = await c.patch(f"/v1/consolidation-specs/{spec}", json={"status": "retired"})
        after = await c.patch(f"/v1/consolidation-specs/{spec}", json={"name": "Sau"})
    assert content.status_code == 200, content.text
    assert content.json()["last_edited_by"] == str(w.users["steward"])
    assert rename.status_code == 200, rename.text
    # Renaming is not editing what the spec computes (BR-156).
    assert rename.json()["last_edited_by"] == str(w.users["steward"])
    assert no_target.status_code == 422, no_target.text
    assert activate.status_code == 200, activate.text
    assert activate.json()["status"] == "active"
    # Setting the target is content: the owner is now the last editor.
    assert activate.json()["last_edited_by"] == str(w.users["owner"])
    assert nulled.status_code == 422, nulled.text
    assert retire.status_code == 200, retire.text
    assert after.status_code == 409, after.text
    updates = [p for t, p in w.events(spec) if t == "acquire.spec.updated"]
    assert [u["content_changed"] for u in updates] == [True, False, True, False]


async def test_patch_spec_is_scoped(w: World) -> None:
    other, _ = w.source(space=w.other_space)
    async with w.client("owner", w.other_space) as c:
        spec = (await c.post("/v1/consolidation-specs", json=_spec_body(other))).json()["id"]
    async with w.client("owner") as c:
        r = await c.patch(f"/v1/consolidation-specs/{spec}", json={"name": "x"})
    assert r.status_code == 404, r.text


@pytest.mark.req("AC-14-05-04")
async def test_consolidation_run_takes_published_runs_and_the_spec_editor(w: World) -> None:
    source, version = w.source()
    older = w.run(source, version, status="published")
    w.run_sql(
        "UPDATE acquire.crawl_run SET finished_at = now() - interval '2 days' WHERE id = %s", older
    )
    latest = w.run(source, version, status="published")
    w.run_sql("UPDATE acquire.crawl_run SET finished_at = now() WHERE id = %s", latest)
    in_review = w.run(source, version)
    target = w.dataset()
    async with w.client("owner") as c:
        spec = (await c.post("/v1/consolidation-specs", json=_spec_body(source))).json()["id"]
        draft = await c.post(f"/v1/consolidation-specs/{spec}/runs")
    async with w.client("steward") as c:
        await c.patch(
            f"/v1/consolidation-specs/{spec}",
            json={"status": "active", "target_dataset_id": str(target)},
        )
    async with w.client("owner") as c:
        unpublished = await c.post(
            f"/v1/consolidation-specs/{spec}/runs", json={"input_run_ids": [str(in_review)]}
        )
        r = await c.post(f"/v1/consolidation-specs/{spec}/runs")
        busy = await c.post(f"/v1/consolidation-specs/{spec}/runs")
        got = await c.get(r.headers["Location"])
    assert draft.status_code == 409, draft.text
    assert unpublished.status_code == 422, unpublished.text
    assert r.status_code == 202, r.text
    run = r.json()["id"]
    assert r.headers["Location"] == f"/v1/consolidation-runs/{run}"
    assert r.json()["status"] == "running"
    assert busy.status_code == 409, busy.text
    assert got.status_code == 200, got.text
    assert got.json()["input_run_ids"] == [str(latest)]
    # BR-156: whoever last changed what the spec computes creates the dataset version.
    assert got.json()["spec_editor_id"] == str(w.users["steward"])
    started = w.event(run, "acquire.consolidation.started")
    assert started["input_run_ids"] == [str(latest)]
    assert started["target_dataset_id"] == str(target)


async def test_consolidation_run_inputs_belong_to_the_spec(w: World) -> None:
    source, version = w.source()
    w.run(source, version, status="published")
    stranger, stranger_v = w.source()
    foreign_run = w.run(stranger, stranger_v, status="published")
    target = w.dataset()
    async with w.client("owner") as c:
        spec = (
            await c.post(
                "/v1/consolidation-specs",
                json=_spec_body(source, target_dataset_id=str(target)),
            )
        ).json()["id"]
        await c.patch(f"/v1/consolidation-specs/{spec}", json={"status": "active"})
        r = await c.post(
            f"/v1/consolidation-specs/{spec}/runs", json={"input_run_ids": [str(foreign_run)]}
        )
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["field"] == "input_run_ids"


async def test_consolidation_routes_are_scoped(w: World) -> None:
    other, other_v = w.source(space=w.other_space)
    w.run(other, other_v, status="published")
    target = w.seed.row(
        "catalog.dataset",
        org_id=w.org,
        data_space_id=w.other_space,
        origin="consolidated",
        schema_name="silver",
        table_name="lai_suat_khac",
        history_kind="snapshot",
        enabled=True,
    )
    async with w.client("owner", w.other_space) as c:
        spec = (
            await c.post(
                "/v1/consolidation-specs",
                json=_spec_body(other, target_dataset_id=str(target)),
            )
        ).json()["id"]
        await c.patch(f"/v1/consolidation-specs/{spec}", json={"status": "active"})
        made = await c.post(f"/v1/consolidation-specs/{spec}/runs")
        assert made.status_code == 202, made.text
    async with w.client("owner") as c:
        start = await c.post(f"/v1/consolidation-specs/{spec}/runs")
        read = await c.get(f"/v1/consolidation-runs/{made.json()['id']}")
    assert (start.status_code, read.status_code) == (404, 404)


# ================================================================ entity links (BR-169)


async def test_list_entity_links_defaults_to_pending_and_is_scoped(w: World) -> None:
    source, _ = w.source()
    pending = w.link(source, key="LPBank")
    rejected = w.link(source, key="Khong ro", status="rejected")
    corp = w.link(source, key="Cong ty A", entity_type="enterprise")
    other, _ = w.source(space=w.other_space)
    w.link(other, space=w.other_space)
    async with w.client("owner") as c:
        default = await c.get("/v1/entity-links")
        banks = await c.get("/v1/entity-links", params={"entity_type": "bank"})
        done = await c.get("/v1/entity-links", params={"status": "rejected"})
        by_source = await c.get("/v1/entity-links", params={"source_id": str(other)})
    assert default.status_code == 200, default.text
    assert {i["id"] for i in default.json()["items"]} == {str(pending), str(corp)}
    assert [i["id"] for i in banks.json()["items"]] == [str(pending)]
    assert [i["id"] for i in done.json()["items"]] == [str(rejected)]
    assert by_source.json()["items"] == []


@pytest.mark.req("AC-14-05-10")
async def test_a_person_confirms_a_fuzzy_match_against_the_catalogue(w: World) -> None:
    source, _ = w.source()
    link = w.link(source)
    reference = w.reference("01201001")
    async with w.client("steward") as c:
        r = await c.post(
            f"/v1/entity-links/{link}:decide",
            json={"status": "confirmed", "reference_entity_id": str(reference)},
        )
        again = await c.post(f"/v1/entity-links/{link}:decide", json={"status": "rejected"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["method"] == "human"
    assert body["decided_by"] == str(w.users["steward"])
    # The canonical key is the catalogue's, not the matcher's guess (BR-169).
    assert body["canonical_key"] == "SBV:01201001"
    assert body["reference_entity_id"] == str(reference)
    decided = w.event(link, "acquire.entity_link.decided")
    assert (decided["status"], decided["canonical_key"]) == ("confirmed", "SBV:01201001")
    assert again.status_code == 409, again.text


@pytest.mark.req("AC-14-05-10")
async def test_entity_link_decision_refusals(w: World) -> None:
    source, _ = w.source()
    link = w.link(source)
    corp = w.reference("01202002", entity_type="enterprise")
    merged = w.reference("01203003", status="merged")
    other, _ = w.source(space=w.other_space)
    hidden = w.link(other, space=w.other_space)
    reference = w.reference("01204004")
    async with w.client("owner") as c:
        no_reference = await c.post(f"/v1/entity-links/{link}:decide", json={"status": "confirmed"})
        wrong_type = await c.post(
            f"/v1/entity-links/{link}:decide",
            json={"status": "confirmed", "reference_entity_id": str(corp)},
        )
        to_merged = await c.post(
            f"/v1/entity-links/{link}:decide",
            json={"status": "confirmed", "reference_entity_id": str(merged)},
        )
        unknown = await c.post(
            f"/v1/entity-links/{link}:decide",
            json={"status": "confirmed", "reference_entity_id": str(uuid.uuid4())},
        )
        outside = await c.post(
            f"/v1/entity-links/{hidden}:decide",
            json={"status": "confirmed", "reference_entity_id": str(reference)},
        )
        rejected = await c.post(f"/v1/entity-links/{link}:decide", json={"status": "rejected"})
    for r in (no_reference, wrong_type, to_merged, unknown):
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "validation_failed"
    assert outside.status_code == 404, outside.text
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["method"] == "human"
