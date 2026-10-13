"""Biến đổi dữ liệu (UC-06-01 to UC-06-06) against PostgreSQL.

Each test builds its own organisation: two data engineers (prepare_data, writeback), an analyst
who only asks, a data admin (admin_data), a lead who both drafts and approves, an approver and a
user without any of those, in one Data Space with a source table, a derived table and a source
that declares a writeback account. The pipeline worker is not running: the tests assert the row
and the event, not the effect.
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

# A token ending in this suffix is the same user acting through an agent (an `act` claim).
AGENT = "#agent"


@dataclass
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    source: uuid.UUID
    readonly_source: uuid.UUID
    raw: uuid.UUID
    derived: uuid.UUID
    other_dataset: uuid.UUID
    other_source: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM pipeline.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def run(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, *spaces: uuid.UUID) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


def _source(seed: Seed, org: uuid.UUID, space: uuid.UUID, writeback: bool) -> uuid.UUID:
    return seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Core banking" if writeback else "DWH chỉ đọc",
        kind="postgresql",
        secret_ref=f"kv/sources/{uuid.uuid4()}",
        writeback_secret_ref=f"kv/writeback/{uuid.uuid4()}" if writeback else None,
        # UC-06-04: the one table the tests write back to is a declared target.
        writeback_tables=["core.du_no_dieu_chinh"] if writeback else [],
    )


def _dataset(
    seed: Seed,
    org: uuid.UUID,
    space: uuid.UUID,
    table: str,
    *,
    origin: str = "derived",
    source: uuid.UUID | None = None,
) -> uuid.UUID:
    return seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        source_id=source,
        origin=origin,
        schema_name="core" if origin == "source" else "ws_tin_dung",
        table_name=table,
        display_name=table.replace("_", " "),
        enabled=True,
    )


@pytest.fixture
def pp(seed: Seed) -> Space:
    org = seed.org()
    engineer = seed.row(
        "org.role",
        org_id=org,
        code="AC04",
        name="Engineer",
        capabilities=["ask", "prepare_data", "writeback"],
    )
    ask = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    data_admin = seed.row(
        "org.role", org_id=org, code="DATA", name="Data", capabilities=["ask", "admin_data"]
    )
    approver = seed.row(
        "org.role", org_id=org, code="AC03", name="Approver", capabilities=["ask", "approve"]
    )
    lead = seed.row(
        "org.role",
        org_id=org,
        code="LEAD",
        name="Lead",
        capabilities=["ask", "admin_data", "approve"],
    )
    nothing = seed.row("org.role", org_id=org, code="UP", name="Upload", capabilities=["upload"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-KT", name="Kế toán", steward_id=keeper)
    users = {
        # The engineer and the approver also work in the other Data Space.
        "eng": _user(seed, org, "eng", engineer, space, other),
        "eng2": _user(seed, org, "eng2", engineer, space),
        "analyst": _user(seed, org, "analyst", ask, space),
        "dataadmin": _user(seed, org, "dataadmin", data_admin, space),
        "approver": _user(seed, org, "approver", approver, space, other),
        "lead": _user(seed, org, "lead", lead, space),
        "nobody": _user(seed, org, "nobody", nothing, space),
    }
    source = _source(seed, org, space, writeback=True)
    readonly = _source(seed, org, space, writeback=False)
    other_source = _source(seed, org, other, writeback=True)
    raw = _dataset(seed, org, space, "du_no_chi_nhanh", origin="source", source=source)
    derived = _dataset(seed, org, space, "du_no_sach")
    other_dataset = _dataset(seed, org, other, "so_cai_ke_toan")
    return Space(
        org, space, other, users, source, readonly, raw, derived, other_dataset, other_source, seed
    )


class Tokens:
    def __init__(self, pp: Space) -> None:
        self.pp = pp

    def claims(self, token: str) -> dict[str, Any]:
        if token.endswith(AGENT):
            return {
                "sub": token.removesuffix(AGENT),
                "org_id": str(self.pp.org),
                "act": {"sub": "agent-chuan-bi-du-lieu"},
            }
        return {"sub": token, "org_id": str(self.pp.org)}


def client(pp: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(pp))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


STEPS = [
    {
        "kind": "filter",
        "description": "Bỏ dòng tổng",
        "code": "SELECT * FROM du_no WHERE ma_chi_nhanh <> 'TONG'",
        "drop_condition": "ma_chi_nhanh = 'TONG'",
    },
    {
        "kind": "cast",
        "description": "Đổi '1.234,5' thành số",
        "code": "SELECT replace(replace(du_no, '.', ''), ',', '.')::numeric AS du_no FROM t",
    },
]


async def _create(
    api: httpx.AsyncClient, pp: Space, who: str = "eng", **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Làm sạch dư nợ",
        "input_dataset_ids": [str(pp.raw)],
        "steps": STEPS,
    }
    body.update(extra)
    made = await api.post("/v1/pipelines", json=body, headers=pp.headers(who))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


def _run(
    pp: Space,
    pipeline: str,
    status: str,
    *,
    mode: str = "preview",
    totals: dict[str, Any] | None = None,
) -> uuid.UUID:
    return pp.seed.row(
        "pipeline.pipeline_run",
        org_id=pp.org,
        pipeline_id=pipeline,
        mode=mode,
        status=status,
        rows_in=50_000,
        rows_out=49_688,
        rows_dropped=Json([{"step": 1, "rows": 312, "condition": "ma_chi_nhanh = 'TONG'"}]),
        totals_check=Json(totals) if totals is not None else None,
    )


def _activate(pp: Space, pipeline: str) -> None:
    # What a materialize does once the worker has written the table.
    pp.run(
        "UPDATE pipeline.pipeline SET status = 'active', target_dataset_id = %s WHERE id = %s",
        pp.derived,
        pipeline,
    )


# --- pipelines -----------------------------------------------------------------------------------


# AC-06-01-01
@pytest.mark.req("AC-06-01-01")
async def test_a_pipeline_is_created_as_a_draft_with_its_steps(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(
            api, pp, instruction="  bỏ dòng tổng, đổi '1.234,5' thành số  ", scope="data_space"
        )
        assert made["status"] == "draft" and made["owner_id"] == str(pp.users["eng"])
        assert made["scope"] == "data_space" and made["cron"] is None
        assert [s["kind"] for s in made["steps"]] == ["filter", "cast"]
        assert made["steps"][0]["drop_condition"] == "ma_chi_nhanh = 'TONG'"
        assert pp.one("SELECT status FROM pipeline.pipeline WHERE id = %s", made["id"]) == "draft"
        events = dict(pp.events(made["id"]))
        assert events["pipeline.created"]["input_dataset_ids"] == [str(pp.raw)]
        assert events["pipeline.steps_requested"]["instruction"] == (
            "bỏ dòng tổng, đổi '1.234,5' thành số"
        )

        twice = await api.post(
            "/v1/pipelines",
            json={"name": "Làm sạch dư nợ", "input_dataset_ids": [str(pp.raw)]},
            headers=pp.headers("eng2"),
        )
        assert twice.status_code == 409, twice.text
        no_words = await _create(api, pp, name="Không lời")
        assert [e for e, _ in pp.events(no_words["id"])] == ["pipeline.created"]


async def test_a_pipeline_reads_only_its_own_data_space(pp: Space) -> None:
    async with client(pp) as api:
        elsewhere = await api.post(
            "/v1/pipelines",
            json={"name": "Lấn sân", "input_dataset_ids": [str(pp.other_dataset)]},
            headers=pp.headers("eng"),
        )
        assert elsewhere.status_code == 422, elsewhere.text
        assert elsewhere.json()["errors"][0]["field"] == "input_dataset_ids"
        no_input = await api.post(
            "/v1/pipelines",
            json={"name": "Trống", "input_dataset_ids": []},
            headers=pp.headers("eng"),
        )
        assert no_input.status_code == 422
        no_prepare = await api.post(
            "/v1/pipelines",
            json={"name": "Hỏi", "input_dataset_ids": [str(pp.raw)]},
            headers=pp.headers("analyst"),
        )
        assert no_prepare.status_code == 403


# AC-06-01-07
# AC-06-01-08
@pytest.mark.req("AC-06-01-07", "AC-06-01-08")
async def test_a_step_never_writes_and_a_dropping_step_names_its_condition(pp: Space) -> None:
    async with client(pp) as api:
        writes = await api.post(
            "/v1/pipelines",
            json={
                "name": "Ghi nguồn",
                "input_dataset_ids": [str(pp.raw)],
                "steps": [
                    {
                        "kind": "compute",
                        "description": "Sửa nguồn",
                        "code": "UPDATE core.du_no SET du_no = 0 -- 'insert' in a comment is fine",
                    }
                ],
            },
            headers=pp.headers("eng"),
        )
        assert writes.status_code == 422, writes.text
        assert writes.json()["code"] == "business_rule_violated"
        assert "update" in writes.json()["detail"]
        # A write keyword inside a string or a comment is not a write.
        quoted = await _create(
            api,
            pp,
            name="Chuỗi",
            steps=[
                {
                    "kind": "compute",
                    "description": "Nhãn",
                    "code": "SELECT 'delete me' AS nhan /* drop */ FROM t",
                }
            ],
        )
        assert quoted["status"] == "draft"
        silent = await api.post(
            "/v1/pipelines",
            json={
                "name": "Lọc ngầm",
                "input_dataset_ids": [str(pp.raw)],
                "steps": [{"kind": "filter", "description": "Bỏ dòng", "code": "SELECT 1"}],
            },
            headers=pp.headers("eng"),
        )
        assert silent.status_code == 422
        assert "drop_condition" in silent.text


async def test_personal_pipelines_are_their_owner_s(pp: Space) -> None:
    async with client(pp) as api:
        mine = await _create(api, pp, name="Của tôi")
        shared = await _create(api, pp, name="Dùng chung", scope="data_space")
        theirs = await _create(api, pp, who="eng2", name="Của eng2")
        listed = await api.get("/v1/pipelines", headers=pp.headers("eng"))
        assert listed.status_code == 200, listed.text
        assert [p["id"] for p in listed.json()["items"]] == [shared["id"], mine["id"]]
        analyst = await api.get("/v1/pipelines", headers=pp.headers("analyst"))
        assert [p["id"] for p in analyst.json()["items"]] == [shared["id"]]
        scoped = await api.get(
            "/v1/pipelines", params={"scope": "personal"}, headers=pp.headers("eng2")
        )
        assert [p["id"] for p in scoped.json()["items"]] == [theirs["id"]]
        _activate(pp, shared["id"])
        active = await api.get(
            "/v1/pipelines", params={"status": "active"}, headers=pp.headers("eng")
        )
        assert [p["id"] for p in active.json()["items"]] == [shared["id"]]
        page = await api.get("/v1/pipelines?page_size=1", headers=pp.headers("eng"))
        rest = await api.get(
            "/v1/pipelines",
            params={"page_token": page.json()["next_page_token"]},
            headers=pp.headers("eng"),
        )
        assert [p["id"] for p in page.json()["items"] + rest.json()["items"]] == [
            shared["id"],
            mine["id"],
        ]
        other_space = await api.get("/v1/pipelines", headers=pp.headers("eng", pp.other))
        assert other_space.json()["items"] == []
        no_ask = await api.get("/v1/pipelines", headers=pp.headers("nobody"))
        assert no_ask.status_code == 403

        # Someone else's personal pipeline does not exist for eng2.
        hidden = await api.patch(
            f"/v1/pipelines/{mine['id']}", json={"name": "x"}, headers=pp.headers("eng2")
        )
        assert hidden.status_code == 404
        hidden_preview = await api.post(
            f"/v1/pipelines/{mine['id']}:preview", headers=pp.headers("eng2")
        )
        assert hidden_preview.status_code == 404


async def test_patch_edits_steps_status_and_dependencies(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(api, pp, scope="data_space")
        upstream = await _create(api, pp, name="Nạp danh mục", scope="data_space")
        url = f"/v1/pipelines/{made['id']}"
        renamed = await api.patch(
            url,
            json={"name": "Làm sạch dư nợ v2", "steps": STEPS[:1], "on_failure": "skip"},
            headers=pp.headers("eng2"),
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Làm sạch dư nợ v2" and len(renamed.json()["steps"]) == 1
        assert renamed.headers["ETag"]
        updated = dict(pp.events(made["id"]))["pipeline.updated"]
        assert updated["changed"] == ["name", "on_failure", "steps"]

        # A1: If-Match is what the client saw; a stale one is 412.
        stale = await api.patch(
            url,
            json={"name": "Cũ"},
            headers={**pp.headers("eng"), "If-Match": f'"{made["updated_at"]}"'},
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["code"] == "precondition_failed"
        fresh = await api.patch(
            url,
            json={"name": "Mới"},
            headers={**pp.headers("eng"), "If-Match": renamed.headers["ETag"]},
        )
        assert fresh.status_code == 200, fresh.text

        taken = await api.patch(url, json={"name": "Nạp danh mục"}, headers=pp.headers("eng"))
        assert taken.status_code == 409
        early = await api.patch(url, json={"status": "active"}, headers=pp.headers("eng"))
        assert early.status_code == 409
        empty = await api.patch(url, json={}, headers=pp.headers("eng"))
        assert empty.status_code == 422
        null = await api.patch(url, json={"name": None}, headers=pp.headers("eng"))
        assert null.status_code == 422
        writes = await api.patch(
            url,
            json={
                "steps": [{"kind": "compute", "description": "x", "code": "TRUNCATE core.du_no"}]
            },
            headers=pp.headers("eng"),
        )
        assert writes.status_code == 422
        assert writes.json()["code"] == "business_rule_violated"

        itself = await api.patch(
            url, json={"depends_on_pipeline_ids": [made["id"]]}, headers=pp.headers("eng")
        )
        assert itself.status_code == 422
        depends = await api.patch(
            url, json={"depends_on_pipeline_ids": [upstream["id"]]}, headers=pp.headers("eng")
        )
        assert depends.status_code == 200, depends.text
        assert depends.json()["depends_on_pipeline_ids"] == [upstream["id"]]
        # BR-112: the dependency graph stays acyclic.
        cycle = await api.patch(
            f"/v1/pipelines/{upstream['id']}",
            json={"depends_on_pipeline_ids": [made["id"]]},
            headers=pp.headers("eng"),
        )
        assert cycle.status_code == 422
        assert cycle.json()["code"] == "business_rule_violated"
        unknown = await api.patch(
            url, json={"depends_on_pipeline_ids": [str(uuid.uuid4())]}, headers=pp.headers("eng")
        )
        assert unknown.status_code == 422

        _activate(pp, made["id"])
        paused = await api.patch(url, json={"status": "paused"}, headers=pp.headers("eng"))
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        changed = [p for e, p in pp.events(made["id"]) if e == "pipeline.status_changed"]
        assert changed[0]["previous_status"] == "active" and changed[0]["status"] == "paused"
        back = await api.patch(url, json={"status": "draft"}, headers=pp.headers("eng"))
        assert back.status_code == 409

        no_prepare = await api.patch(url, json={"name": "y"}, headers=pp.headers("analyst"))
        assert no_prepare.status_code == 403
        other_space = await api.patch(url, json={"name": "y"}, headers=pp.headers("eng", pp.other))
        assert other_space.status_code == 404


async def test_preview_runs_on_a_sample(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(api, pp)
        url = f"/v1/pipelines/{made['id']}:preview"
        default = await api.post(url, headers=pp.headers("eng"))
        assert default.status_code == 202, default.text
        run = default.json()
        assert default.headers["Location"] == f"/v1/pipeline-runs/{run['id']}"
        assert run["mode"] == "preview" and run["sample_rows"] == 10_000
        assert run["status"] == "running"
        assert pp.one("SELECT mode FROM pipeline.pipeline_run WHERE id = %s", run["id"]) == (
            "preview"
        )
        [(event, payload)] = pp.events(run["id"])
        assert event == "pipeline_run.requested"
        assert payload["pipeline_id"] == made["id"] and payload["sample_rows"] == 10_000

        smaller = await api.post(url, json={"sample_rows": 500}, headers=pp.headers("eng"))
        assert smaller.status_code == 202 and smaller.json()["sample_rows"] == 500
        too_many = await api.post(url, json={"sample_rows": 10_001}, headers=pp.headers("eng"))
        assert too_many.status_code == 422
        no_prepare = await api.post(url, headers=pp.headers("analyst"))
        assert no_prepare.status_code == 403

        bare = await _create(api, pp, name="Chưa có bước", steps=[])
        nothing = await api.post(f"/v1/pipelines/{bare['id']}:preview", headers=pp.headers("eng"))
        assert nothing.status_code == 409


# AC-06-01-06
# AC-06-02-03
@pytest.mark.req("AC-06-01-06", "AC-06-02-03")
async def test_materialize_needs_a_good_run_and_a_free_name(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(api, pp)
        url = f"/v1/pipelines/{made['id']}:materialize"
        body = {"table_name": "du_no_sach_v2", "scope": "data_space"}
        never_run = await api.post(url, json=body, headers=pp.headers("eng"))
        assert never_run.status_code == 409, never_run.text

        _run(pp, made["id"], "failed")
        failed = await api.post(url, json=body, headers=pp.headers("eng"))
        assert failed.status_code == 409

        _run(
            pp,
            made["id"],
            "succeeded",
            totals={
                "matched": False,
                "columns": [{"column": "du_no", "matched": False, "before": 10, "after": 12}],
            },
        )
        # UC-06-01 E2: totals differ, nothing is saved.
        mismatch = await api.post(url, json=body, headers=pp.headers("eng"))
        assert mismatch.status_code == 422, mismatch.text
        assert mismatch.json()["code"] == "business_rule_violated"
        assert "du_no" in mismatch.json()["detail"]
        assert (
            pp.one(
                "SELECT count(*) FROM pipeline.pipeline_run WHERE pipeline_id = %s AND mode = 'full'",
                made["id"],
            )
            == 0
        )

        _run(pp, made["id"], "succeeded", totals={"matched": True, "columns": []})
        # UC-06-02 E1: another table of the work schema has the name.
        taken = await api.post(url, json={"table_name": "du_no_sach"}, headers=pp.headers("eng"))
        assert taken.status_code == 409, taken.text
        bad_name = await api.post(
            url, json={"table_name": "Du-No; drop"}, headers=pp.headers("eng")
        )
        assert bad_name.status_code == 422
        no_prepare = await api.post(url, json=body, headers=pp.headers("analyst"))
        assert no_prepare.status_code == 403

        saved = await api.post(url, json=body, headers=pp.headers("eng"))
        assert saved.status_code == 202, saved.text
        run = saved.json()["id"]
        assert saved.headers["Location"] == f"/v1/pipeline-runs/{run}"
        assert pp.one("SELECT mode FROM pipeline.pipeline_run WHERE id = %s", run) == "full"
        assert pp.one("SELECT scope FROM pipeline.pipeline WHERE id = %s", made["id"]) == (
            "data_space"
        )
        [(event, payload)] = pp.events(run)
        assert event == "pipeline.materialize_requested"
        assert payload["table_name"] == "du_no_sach_v2" and payload["scope"] == "data_space"
        assert payload["overwrite_dataset_id"] is None

        busy = await api.post(url, json=body, headers=pp.headers("eng"))
        assert busy.status_code == 409


async def test_materialize_overwrites_its_own_table(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(api, pp)
        _activate(pp, made["id"])
        _run(pp, made["id"], "succeeded", totals={"matched": True})
        # UC-06-02 1a: the pipeline's own table is overwritten as a new version.
        again = await api.post(
            f"/v1/pipelines/{made['id']}:materialize",
            json={"table_name": "du_no_sach"},
            headers=pp.headers("eng"),
        )
        assert again.status_code == 202, again.text
        payload = dict(pp.events(again.json()["id"]))["pipeline.materialize_requested"]
        assert payload["overwrite_dataset_id"] == str(pp.derived)


# AC-06-03-01
@pytest.mark.req("AC-06-03-01")
async def test_pipeline_authoring_and_schedule_reads_are_canonical(pp: Space) -> None:
    async with client(pp) as api:
        made = await _create(api, pp, scope="data_space")
        pipeline_id = made["id"]
        step = await api.get(
            f"/v1/pipelines/{pipeline_id}/steps/1/preview", headers=pp.headers("eng")
        )
        assert step.status_code == 200 and step.json()["seq"] == 1
        draft = await api.get(
            f"/v1/pipelines/{pipeline_id}/output-draft", headers=pp.headers("eng")
        )
        assert draft.status_code == 200 and draft.json()["data_space_id"] == str(pp.space)
        message = await api.post(
            f"/v1/pipelines/{pipeline_id}/messages",
            json={"text": "giữ lại các dòng hợp lệ"},
            headers=pp.headers("eng"),
        )
        assert message.status_code == 200
        assert dict(pp.events(pipeline_id))["pipeline.message_requested"]["text"] == (
            "giữ lại các dòng hợp lệ"
        )
        _activate(pp, pipeline_id)
        schedule = await api.get(f"/v1/pipelines/{pipeline_id}/schedule", headers=pp.headers("eng"))
        assert schedule.status_code == 200 and schedule.json()["cron"] is None
        preview = await api.post(
            f"/v1/pipelines/{pipeline_id}/schedule:preview",
            json={"cron": "0 7 * * 1-5", "no_overlap": True},
            headers=pp.headers("eng"),
        )
        assert preview.status_code == 200 and "0 7" in preview.json()["text"]


async def test_an_active_pipeline_is_scheduled(pp: Space) -> None:
    calendar = pp.seed.row("org.work_calendar", org_id=pp.org, code="VN", name="Lịch ngân hàng")
    job = uuid.uuid4()
    async with client(pp) as api:
        made = await _create(api, pp, scope="data_space")
        upstream = await _create(api, pp, name="Nạp danh mục", scope="data_space")
        url = f"/v1/pipelines/{made['id']}/schedule"
        body = {
            "cron": "0 7 * * 1-5",
            "work_calendar_id": str(calendar),
            "trigger_on_job_id": str(job),
            "depends_on_pipeline_ids": [upstream["id"]],
            "no_overlap": True,
            "on_failure": "retry_then_notify",
        }
        draft = await api.put(url, json=body, headers=pp.headers("eng"))
        assert draft.status_code == 409, draft.text
        _activate(pp, made["id"])

        scheduled = await api.put(url, json=body, headers=pp.headers("eng"))
        assert scheduled.status_code == 200, scheduled.text
        out = scheduled.json()
        assert out["cron"] == "0 7 * * 1-5" and out["work_calendar_id"] == str(calendar)
        assert out["trigger_on_job_id"] == str(job) and out["on_failure"] == "retry_then_notify"
        assert pp.one("SELECT cron FROM pipeline.pipeline WHERE id = %s", made["id"]) == (
            "0 7 * * 1-5"
        )
        payload = dict(pp.events(made["id"]))["pipeline.scheduled"]
        assert payload["depends_on_pipeline_ids"] == [upstream["id"]]
        assert payload["work_calendar_id"] == str(calendar)

        # PUT replaces the whole schedule.
        cleared = await api.put(url, json={}, headers=pp.headers("eng"))
        assert cleared.json()["cron"] is None and cleared.json()["depends_on_pipeline_ids"] == []

        bad_cron = await api.put(url, json={"cron": "every day"}, headers=pp.headers("eng"))
        assert bad_cron.status_code == 422
        bad_calendar = await api.put(
            url, json={"work_calendar_id": str(uuid.uuid4())}, headers=pp.headers("eng")
        )
        assert bad_calendar.status_code == 422
        _activate(pp, upstream["id"])
        await api.put(url, json=body, headers=pp.headers("eng"))
        cycle = await api.put(
            f"/v1/pipelines/{upstream['id']}/schedule",
            json={"depends_on_pipeline_ids": [made["id"]]},
            headers=pp.headers("eng"),
        )
        assert cycle.status_code == 422
        no_prepare = await api.put(url, json=body, headers=pp.headers("analyst"))
        assert no_prepare.status_code == 403
        other_space = await api.put(url, json=body, headers=pp.headers("eng", pp.other))
        assert other_space.status_code == 404


# --- lineage -------------------------------------------------------------------------------------


@pytest.mark.req("AC-06-05-00")
async def test_lineage_targets_lists_visible_datasets(pp: Space) -> None:
    async with client(pp) as api:
        response = await api.get("/v1/lineage/targets", headers=pp.headers("analyst"))
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert str(pp.raw) in ids
    assert str(pp.derived) in ids
    assert str(pp.other_dataset) not in ids


def _edge(
    pp: Space,
    from_: tuple[str, uuid.UUID],
    to: tuple[str, uuid.UUID],
    *,
    space: uuid.UUID | None = None,
    via: str | None = "pipeline",
) -> uuid.UUID:
    return pp.seed.row(
        "pipeline.lineage_edge",
        org_id=pp.org,
        data_space_id=space or pp.space,
        from_kind=from_[0],
        from_id=from_[1],
        to_kind=to[0],
        to_id=to[1],
        via=via,
        via_ref=uuid.uuid4(),
    )


# AC-06-05-01
# AC-06-05-02
@pytest.mark.req("AC-06-05-01", "AC-06-05-02")
async def test_lineage_hides_what_the_caller_may_not_see(pp: Space) -> None:
    metric, report = uuid.uuid4(), uuid.uuid4()
    _edge(pp, ("source", pp.source), ("dataset", pp.raw), via="scan")
    _edge(pp, ("dataset", pp.raw), ("dataset", pp.derived))
    _edge(pp, ("dataset", pp.derived), ("metric", metric), via="metric_version")
    _edge(pp, ("metric", metric), ("report", report), via="report")
    # The other Data Space builds its ledger table on our derived table.
    _edge(pp, ("dataset", pp.derived), ("dataset", pp.other_dataset), space=pp.other)
    # A retired edge is history, not lineage.
    pp.run(
        "UPDATE pipeline.lineage_edge SET valid_to = now() WHERE id = %s",
        _edge(pp, ("dataset", pp.raw), ("metric", uuid.uuid4())),
    )
    async with client(pp) as api:
        up = await api.get(
            "/v1/lineage",
            params={"object_kind": "metric", "object_id": str(metric)},
            headers=pp.headers("analyst"),
        )
        assert up.status_code == 200, up.text
        graph = up.json()
        assert graph["depth"] == 3 and graph["direction"] == "upstream"
        assert {n["key"] for n in graph["nodes"]} == {
            f"metric:{metric}",
            f"dataset:{pp.derived}",
            f"dataset:{pp.raw}",
            f"source:{pp.source}",
        }
        names = {n["key"]: n["name"] for n in graph["nodes"]}
        assert names[f"dataset:{pp.derived}"] == "du no sach"
        assert len(graph["edges"]) == 3

        shallow = await api.get(
            "/v1/lineage",
            params={"object_kind": "metric", "object_id": str(metric), "depth": 1},
            headers=pp.headers("analyst"),
        )
        assert len(shallow.json()["nodes"]) == 2

        # UC-06-05 1a: everything built on the table.
        down = await api.get(
            "/v1/lineage",
            params={
                "object_kind": "dataset",
                "object_id": str(pp.derived),
                "direction": "downstream",
            },
            headers=pp.headers("analyst"),
        )
        nodes = down.json()["nodes"]
        shown = {n["key"] for n in nodes if not n["hidden"]}
        assert shown == {f"dataset:{pp.derived}", f"metric:{metric}", f"report:{report}"}
        [hidden] = [n for n in nodes if n["hidden"]]
        assert hidden["id"] is None and hidden["name"] == "Đối tượng bạn không có quyền xem"
        assert str(pp.other_dataset) not in down.text
        [to_hidden] = [e for e in down.json()["edges"] if e["target"] == hidden["key"]]
        assert to_hidden["via"] is None and to_hidden["via_ref"] is None

        foreign = await api.get(
            "/v1/lineage",
            params={"object_kind": "dataset", "object_id": str(pp.other_dataset)},
            headers=pp.headers("analyst"),
        )
        assert foreign.status_code == 404
        unknown = await api.get(
            "/v1/lineage",
            params={"object_kind": "metric", "object_id": str(uuid.uuid4())},
            headers=pp.headers("analyst"),
        )
        assert unknown.status_code == 404
        # A dataset of the Data Space with no edge yet is a graph of one node.
        alone = await api.get(
            "/v1/lineage",
            params={"object_kind": "dataset", "object_id": str(pp.raw), "direction": "upstream"},
            headers=pp.headers("analyst"),
        )
        assert alone.status_code == 200
        too_deep = await api.get(
            "/v1/lineage",
            params={"object_kind": "metric", "object_id": str(metric), "depth": 11},
            headers=pp.headers("analyst"),
        )
        assert too_deep.status_code == 422
        no_ask = await api.get(
            "/v1/lineage",
            params={"object_kind": "metric", "object_id": str(metric)},
            headers=pp.headers("nobody"),
        )
        assert no_ask.status_code == 403


# AC-06-05-03
@pytest.mark.req("AC-06-05-03")
async def test_a_large_lineage_graph_is_grouped_by_kind(pp: Space) -> None:
    pp.run(
        "INSERT INTO pipeline.lineage_edge (id, org_id, data_space_id, from_kind, from_id,"
        " to_kind, to_id, via) SELECT gen_random_uuid(), %s, %s, 'dataset', %s,"
        " CASE WHEN g %% 2 = 0 THEN 'metric' ELSE 'tile' END, gen_random_uuid(), 'x'"
        " FROM generate_series(1, 620) g",
        pp.org,
        pp.space,
        pp.derived,
    )
    async with client(pp) as api:
        got = await api.get(
            "/v1/lineage",
            params={
                "object_kind": "dataset",
                "object_id": str(pp.derived),
                "direction": "downstream",
            },
            headers=pp.headers("analyst"),
        )
    assert got.status_code == 200, got.text
    graph = got.json()
    assert graph["grouped"] is True and graph["edges"] == []
    assert {g["kind"]: g["count"] for g in graph["groups"]} == {"metric": 310, "tile": 310}


# --- data marts ----------------------------------------------------------------------------------


async def test_a_mart_is_a_draft_until_its_relations_pass(pp: Space) -> None:
    body = {
        "name": "fact_loan_daily",
        "fact_dataset_id": str(pp.derived),
        "grain": "khoản vay theo ngày",
        "measures": [{"name": "du_no", "agg": "sum"}],
        "dimensions": [{"name": "chi_nhanh", "key": "ma_chi_nhanh"}],
        "relations": [{"from": "ma_chi_nhanh", "to": "dim_chi_nhanh.ma"}],
        "checks": [{"kind": "total_matches_source"}],
    }
    async with client(pp) as api:
        no_admin = await api.post("/v1/marts", json=body, headers=pp.headers("eng"))
        assert no_admin.status_code == 403
        made = await api.post("/v1/marts", json=body, headers=pp.headers("dataadmin"))
        assert made.status_code == 201, made.text
        mart = made.json()
        assert mart["status"] == "draft" and mart["relation_check_passed"] is False
        assert mart["measures"] == body["measures"] and mart["grain"] == "khoản vay theo ngày"
        assert (
            pp.one("SELECT relations FROM pipeline.data_mart WHERE id = %s", mart["id"])
            == (body["relations"])
        )
        [(event, payload)] = pp.events(mart["id"])
        assert event == "data_mart.created" and payload["fact_dataset_id"] == str(pp.derived)

        elsewhere = await api.post(
            "/v1/marts",
            json={**body, "fact_dataset_id": str(pp.other_dataset)},
            headers=pp.headers("dataadmin"),
        )
        assert elsewhere.status_code == 422
        no_grain = await api.post(
            "/v1/marts",
            json={k: v for k, v in body.items() if k != "grain"},
            headers=pp.headers("dataadmin"),
        )
        assert no_grain.status_code == 422


# --- shared data products ------------------------------------------------------------------------


CONTRACT = {
    "columns": [
        {"name": "ngay", "type": "date"},
        {"name": "du_no", "type": "numeric"},
    ]
}


async def _product(
    api: httpx.AsyncClient, pp: Space, who: str = "dataadmin", **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "layer": "published",
        "code": "du_no_hang_ngay",
        "name": "Dư nợ hằng ngày",
        "dataset_id": str(pp.derived),
        "contract": CONTRACT,
        # The owner approves publishing (12/10/2026); the drafter never does.
        "owner_id": str(pp.users["approver"]),
    }
    body.update(extra)
    made = await api.post("/v1/data-products", json=body, headers=pp.headers(who))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


# AC-06-06-01
@pytest.mark.req("AC-06-06-01")
async def test_the_owner_publishes_what_the_engineer_drafted(pp: Space) -> None:
    async with client(pp) as api:
        product = await _product(api, pp)
        url = f"/v1/data-products/{product['id']}:publish"
        no_approve = await api.post(url, headers=pp.headers("dataadmin"))
        assert no_approve.status_code == 403
        other_space = await api.post(url, headers=pp.headers("approver", pp.other))
        assert other_space.status_code == 404

        published = await api.post(url, headers=pp.headers("approver"))
        assert published.status_code == 200, published.text
        out = published.json()
        assert out["status"] == "published" and out["approved_by"] == str(pp.users["approver"])
        assert out["layer"] == "published" and out["version"] == 1
        assert pp.one("SELECT status FROM pipeline.data_product WHERE id = %s", product["id"]) == (
            "published"
        )
        payload = dict(pp.events(product["id"]))["data_product.published"]
        assert payload["approved_by"] == str(pp.users["approver"]) and payload["replaces"] is None

        twice = await api.post(url, headers=pp.headers("approver"))
        assert twice.status_code == 409
        missing = await api.post(
            f"/v1/data-products/{uuid.uuid4()}:publish", headers=pp.headers("approver")
        )
        assert missing.status_code == 404

        own = await _product(api, pp, who="lead", code="tu_lam_tu_duyet")
        self_approved = await api.post(
            f"/v1/data-products/{own['id']}:publish", headers=pp.headers("lead")
        )
        assert self_approved.status_code == 403, self_approved.text


# AC-06-06-03
@pytest.mark.req("AC-06-06-03")
async def test_a_mart_whose_relations_failed_is_not_published(pp: Space) -> None:
    async with client(pp) as api:
        mart = await api.post(
            "/v1/marts",
            json={"name": "fact_loan_daily", "fact_dataset_id": str(pp.derived), "grain": "x"},
            headers=pp.headers("dataadmin"),
        )
        product = await _product(
            api, pp, code="mart_du_no", dataset_id=None, data_mart_id=mart.json()["id"]
        )
        refused = await api.post(
            f"/v1/data-products/{product['id']}:publish", headers=pp.headers("approver")
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        pp.run(
            "UPDATE pipeline.data_mart SET relation_check_passed = true WHERE id = %s",
            mart.json()["id"],
        )
        passed = await api.post(
            f"/v1/data-products/{product['id']}:publish", headers=pp.headers("approver")
        )
        assert passed.status_code == 200, passed.text


# AC-06-06-02
# AC-06-06-04
@pytest.mark.req("AC-06-06-02", "AC-06-06-04")
async def test_a_breaking_change_waits_thirty_days(pp: Space) -> None:
    async with client(pp) as api:
        v1 = await _product(api, pp)
        assert (
            await api.post(f"/v1/data-products/{v1['id']}:publish", headers=pp.headers("approver"))
        ).status_code == 200
        consumers = [str(pp.other), str(uuid.uuid4())]
        pp.run(
            "UPDATE pipeline.data_product SET consumers = %s::uuid[] WHERE id = %s",
            "{" + ",".join(consumers) + "}",
            v1["id"],
        )
        breaking = {
            "columns": [{"name": "ngay", "type": "date"}, {"name": "du_no", "type": "text"}]
        }
        v2 = await _product(api, pp, contract=breaking)
        assert v2["version"] == 2 and v2["breaking_notice_at"] is not None
        assert sorted(v2["consumers"]) == sorted(consumers)
        created = dict(pp.events(v2["id"]))["data_product.created"]
        # The two consumers are told now, thirty days ahead.
        assert created["breaking"] is True and sorted(created["consumers"]) == sorted(consumers)

        early = await api.post(
            f"/v1/data-products/{v2['id']}:publish", headers=pp.headers("approver")
        )
        assert early.status_code == 422, early.text
        assert "du_no" in early.json()["detail"]
        assert pp.one("SELECT status FROM pipeline.data_product WHERE id = %s", v1["id"]) == (
            "published"
        )

        pp.run(
            "UPDATE pipeline.data_product SET breaking_notice_at = now() - interval '31 days'"
            " WHERE id = %s",
            v2["id"],
        )
        on_time = await api.post(
            f"/v1/data-products/{v2['id']}:publish", headers=pp.headers("approver")
        )
        assert on_time.status_code == 200, on_time.text
        assert dict(pp.events(v2["id"]))["data_product.published"]["replaces"] == v1["id"]
        assert pp.one("SELECT status FROM pipeline.data_product WHERE id = %s", v1["id"]) == (
            "deprecated"
        )
        assert [e for e, _ in pp.events(v1["id"])][-1] == "data_product.deprecated"
        # BR-60: the old version stops at least 30 days after the notice.
        assert pp.one(
            "SELECT o.retire_after >= n.breaking_notice_at + interval '30 days'"
            " FROM pipeline.data_product o, pipeline.data_product n WHERE o.id = %s AND n.id = %s",
            v1["id"],
            v2["id"],
        )


async def test_an_added_column_is_published_at_once(pp: Space) -> None:
    async with client(pp) as api:
        v1 = await _product(api, pp)
        await api.post(f"/v1/data-products/{v1['id']}:publish", headers=pp.headers("approver"))
        wider = {"columns": [*CONTRACT["columns"], {"name": "chi_nhanh", "type": "text"}]}
        v2 = await _product(api, pp, contract=wider)
        assert v2["breaking_notice_at"] is None
        published = await api.post(
            f"/v1/data-products/{v2['id']}:publish", headers=pp.headers("approver")
        )
        assert published.status_code == 200, published.text
        assert pp.one("SELECT status FROM pipeline.data_product WHERE id = %s", v1["id"]) == (
            "deprecated"
        )


# --- writebacks ----------------------------------------------------------------------------------


def _writeback_body(pp: Space, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source_id": str(pp.source),
        "target_table": "core.du_no_dieu_chinh",
        "from_dataset_id": str(pp.derived),
    }
    body.update(extra)
    return body


async def test_a_writeback_request_starts_as_a_draft_awaiting_its_diff(pp: Space) -> None:
    window = {"start": "2026-10-13T22:00:00+07:00", "end": "2026-10-14T02:00:00+07:00"}
    async with client(pp) as api:
        dry = await api.post(
            "/v1/writebacks?dry_run=true",
            json=_writeback_body(pp),
            headers=pp.headers("eng"),
        )
        assert dry.status_code == 200, dry.text
        assert (
            pp.one("SELECT count(*) FROM pipeline.writeback_request WHERE org_id = %s", pp.org) == 0
        )

        made = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, write_window=window),
            headers=pp.headers("eng"),
        )
        assert made.status_code == 201, made.text
        out = made.json()
        assert out["status"] == "draft" and out["requested_by"] == str(pp.users["eng"])
        assert out["deletes"] == 0 and out["approved_by"] is None
        assert datetime.datetime.fromisoformat(out["write_window"]["start"]) == (
            datetime.datetime.fromisoformat(window["start"])
        )
        assert pp.one(
            "SELECT upper(write_window) - lower(write_window) FROM pipeline.writeback_request"
            " WHERE id = %s",
            out["id"],
        ) == datetime.timedelta(hours=4)
        [(event, payload)] = pp.events(out["id"])
        assert event == "writeback.diff_requested" and payload["source_id"] == str(pp.source)

        backwards = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, write_window={"start": window["end"], "end": window["start"]}),
            headers=pp.headers("eng"),
        )
        assert backwards.status_code == 422


async def test_a_writeback_needs_a_writeback_account_and_a_derived_table(pp: Space) -> None:
    async with client(pp) as api:
        no_account = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, source_id=str(pp.readonly_source)),
            headers=pp.headers("eng"),
        )
        assert no_account.status_code == 403, no_account.text
        from_source = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, from_dataset_id=str(pp.raw)),
            headers=pp.headers("eng"),
        )
        assert from_source.status_code == 422
        elsewhere = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, source_id=str(pp.other_source)),
            headers=pp.headers("eng"),
        )
        assert elsewhere.status_code == 422
        foreign_table = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, from_dataset_id=str(pp.other_dataset)),
            headers=pp.headers("eng"),
        )
        assert foreign_table.status_code == 422
        no_writeback = await api.post(
            "/v1/writebacks", json=_writeback_body(pp), headers=pp.headers("dataadmin")
        )
        assert no_writeback.status_code == 403
    assert pp.one("SELECT count(*) FROM pipeline.writeback_request WHERE org_id = %s", pp.org) == 0


# AC-06-04-07
@pytest.mark.req("AC-06-04-07")
async def test_an_agent_never_requests_a_writeback(pp: Space) -> None:
    async with client(pp) as api:
        made = await api.post("/v1/writebacks", json=_writeback_body(pp), headers=pp.headers("eng"))
        wid = made.json()["id"]
        for path, body in (
            ("/v1/writebacks", _writeback_body(pp)),
            (f"/v1/writebacks/{wid}:submit", None),
            (f"/v1/writebacks/{wid}:undo", {"reason": "agent"}),
        ):
            refused = await api.post(path, json=body, headers=pp.headers("eng" + AGENT))
            assert refused.status_code == 403, (path, refused.text)
    assert pp.one("SELECT count(*) FROM pipeline.writeback_request WHERE org_id = %s", pp.org) == 1


# AC-06-04-05
# AC-06-04-06
@pytest.mark.req("AC-06-04-05", "AC-06-04-06")
async def test_submit_sends_a_previewed_diff_without_deletes_for_review(pp: Space) -> None:
    async with client(pp) as api:
        made = (
            await api.post("/v1/writebacks", json=_writeback_body(pp), headers=pp.headers("eng"))
        ).json()
        url = f"/v1/writebacks/{made['id']}:submit"
        not_previewed = await api.post(url, headers=pp.headers("eng"))
        assert not_previewed.status_code == 409, not_previewed.text

        pp.run(
            "UPDATE pipeline.writeback_request SET status = 'previewed', diff_summary = %s"
            " WHERE id = %s",
            Json({"inserts": 12_000, "updates": 40, "deletes": 1}),
            made["id"],
        )
        deletes = await api.post(url, headers=pp.headers("eng"))
        assert deletes.status_code == 422, deletes.text
        assert deletes.json()["code"] == "business_rule_violated"

        pp.run(
            "UPDATE pipeline.writeback_request SET diff_summary = %s WHERE id = %s",
            Json({"inserts": 12_000, "updates": 40, "deletes": 0}),
            made["id"],
        )
        someone_else = await api.post(url, headers=pp.headers("eng2"))
        assert someone_else.status_code == 403
        other_space = await api.post(url, headers=pp.headers("eng", pp.other))
        assert other_space.status_code == 404

        sent = await api.post(url, headers=pp.headers("eng"))
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "submitted" and sent.json()["proposal_id"]
        payload = dict(pp.events(made["id"]))["writeback.submitted"]
        assert payload["proposal_id"] == sent.json()["proposal_id"]
        # BR-24: the requester never reviews their own write.
        assert payload["excluded_reviewer_ids"] == [str(pp.users["eng"])]
        again = await api.post(url, headers=pp.headers("eng"))
        assert again.status_code == 409

    # The same rule held by the database.
    with pytest.raises(Exception, match="writeback_request"):
        pp.run(
            "UPDATE pipeline.writeback_request SET approved_by = requested_by WHERE id = %s",
            made["id"],
        )


# AC-06-04-03
@pytest.mark.req("AC-06-04-03")
async def test_an_executed_write_is_undone_within_thirty_days(pp: Space) -> None:
    async with client(pp) as api:
        made = (
            await api.post("/v1/writebacks", json=_writeback_body(pp), headers=pp.headers("eng"))
        ).json()
        url = f"/v1/writebacks/{made['id']}:undo"
        not_executed = await api.post(url, json={"reason": "sai kỳ"}, headers=pp.headers("eng"))
        assert not_executed.status_code == 409

        pp.run(
            "UPDATE pipeline.writeback_request SET status = 'executed', approved_by = %s,"
            " executed_at = now() - interval '10 days', snapshot_ref = 's3://snap/1' WHERE id = %s",
            pp.users["approver"],
            made["id"],
        )
        no_reason = await api.post(url, json={}, headers=pp.headers("eng"))
        assert no_reason.status_code == 422
        no_writeback = await api.post(url, json={"reason": "x"}, headers=pp.headers("analyst"))
        assert no_writeback.status_code == 403
        undo = await api.post(url, json={"reason": "  ghi nhầm kỳ  "}, headers=pp.headers("eng"))
        assert undo.status_code == 202, undo.text
        assert undo.headers["Location"] == f"/v1/writebacks/{made['id']}"
        payload = dict(pp.events(made["id"]))["writeback.undo_requested"]
        assert payload["reason"] == "ghi nhầm kỳ"
        assert payload["excluded_reviewer_ids"] == [str(pp.users["eng"])]
        # undo_requested (12/10/2026): the undo is not asked twice.
        assert (
            pp.one("SELECT status FROM pipeline.writeback_request WHERE id = %s", made["id"])
            == "undo_requested"
        )
        twice = await api.post(url, json={"reason": "lần nữa"}, headers=pp.headers("eng"))
        assert twice.status_code == 409

        pp.run(
            "UPDATE pipeline.writeback_request SET status = 'executed',"
            " executed_at = now() - interval '31 days' WHERE id = %s",
            made["id"],
        )
        late = await api.post(url, json={"reason": "muộn"}, headers=pp.headers("eng"))
        assert late.status_code == 409


async def test_only_the_named_owner_publishes(pp: Space) -> None:
    # 12/10/2026: pipeline.data_product.owner_id approves publishing; the drafter never owns.
    async with client(pp) as api:
        self_owned = await api.post(
            "/v1/data-products",
            json={
                "layer": "published",
                "code": "tu_so_huu",
                "name": "Tự sở hữu",
                "dataset_id": str(pp.derived),
                "contract": CONTRACT,
                "owner_id": str(pp.users["dataadmin"]),
            },
            headers=pp.headers("dataadmin"),
        )
        assert self_owned.status_code == 422
        assert self_owned.json()["errors"][0]["field"] == "owner_id"
        product = await _product(api, pp, code="chu_khac", owner_id=str(pp.users["eng"]))
        not_owner = await api.post(
            f"/v1/data-products/{product['id']}:publish", headers=pp.headers("approver")
        )
        assert not_owner.status_code == 403 and not_owner.json()["code"] == "policy_denied"


async def test_a_writeback_goes_only_to_a_declared_target(pp: Space) -> None:
    # UC-06-04: catalog.source.writeback_tables lists the tables a writeback may write.
    async with client(pp) as api:
        other = await api.post(
            "/v1/writebacks",
            json=_writeback_body(pp, target_table="core.khach_hang"),
            headers=pp.headers("eng"),
        )
        assert other.status_code == 422 and other.json()["code"] == "business_rule_violated"
