"""Nạp dữ liệu against PostgreSQL: ingestion jobs and their runs, the DLQ, CSV/Excel uploads and
data quality rules (UC-04-06 to UC-04-09, UC-06-07).

Each test builds its own organisation with two Data Spaces. The data engineer is a member of
both, so a row of space A read with X-Data-Space B proves the space filter rather than the
membership check. Workers are not running: the tests assert the outbox event, not its effect.
Uploads go to an in-memory object store injected over the module's port.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.ingest import routes as ingest_routes
from services.api.modules.ingest.storage import BUCKET, object_store

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


class FakeStore:
    """The da-ingest bucket in memory; refuses a second write of a key (raw is immutable)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        assert (bucket, key) not in self.objects, "an original was rewritten"
        body = data.read()
        assert len(body) == size
        self.objects[(bucket, key)] = (body, content_type)


@dataclass
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    source: uuid.UUID
    federated: uuid.UUID
    other_source: uuid.UUID
    dataset: uuid.UUID
    upload_dataset: uuid.UUID
    other_dataset: uuid.UUID
    seed: Seed
    store: FakeStore = field(default_factory=FakeStore)

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM ingest.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def job(self, space: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
        values.setdefault("name", "Lãi suất huy động")
        values.setdefault("mode", "full")
        values.setdefault("source_object", "/api/lai-suat")
        return self.seed.row(
            "ingest.ingestion_job",
            org_id=self.org,
            data_space_id=space or self.space,
            source_id=self.source if space in (None, self.space) else self.other_source,
            **values,
        )

    def run(self, job: uuid.UUID, **values: Any) -> uuid.UUID:
        return self.seed.row("ingest.job_run", org_id=self.org, job_id=job, **values)

    def dlq(self, job: uuid.UUID, run: uuid.UUID, **values: Any) -> uuid.UUID:
        values.setdefault("payload_ref", "da-ingest/dlq/x.json")
        values.setdefault("error_kind", "source_data")
        values.setdefault("error", "ngày sai định dạng")
        return self.seed.row(
            "ingest.dlq_record", org_id=self.org, job_id=job, job_run_id=run, **values
        )

    def upload(self, space: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
        values.setdefault("status", "analyzed")
        return self.seed.row(
            "ingest.file_upload",
            org_id=self.org,
            data_space_id=space or self.space,
            uploader_id=self.users["uploader"],
            file_name="du_no.xlsx",
            object_key=f"{self.org}/{space or self.space}/{uuid.uuid4()}.xlsx",
            size_bytes=2048,
            **values,
        )


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


def _dataset(seed: Seed, org: uuid.UUID, space: uuid.UUID, table: str, **values: Any) -> uuid.UUID:
    values.setdefault("origin", "consolidated")
    return seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        schema_name="silver",
        table_name=table,
        enabled=True,
        **values,
    )


@pytest.fixture
def ds(seed: Seed) -> Space:
    org = seed.org()

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row("org.role", org_id=org, code=code, name=code, capabilities=list(caps))

    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-ING", name="Ingest", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-ING-B", name="B", steward_id=keeper)
    users = {
        "engineer": _user(seed, org, "engineer", role("AC04", "ask", "admin_data"), space, other),
        "owner": _user(seed, org, "owner", role("OWNER", "ask", "admin_data", "approve"), space),
        "uploader": _user(seed, org, "uploader", role("AC01", "ask", "upload"), space, other),
        "analyst": _user(seed, org, "analyst", role("ASK", "ask"), space),
    }

    def source(target: uuid.UUID, name: str, mode: str, kind: str) -> uuid.UUID:
        return seed.row(
            "catalog.source",
            org_id=org,
            data_space_id=target,
            name=name,
            kind=kind,
            secret_ref=f"bao://source/{name}",
            mode=mode,
            freshness_sla="1 hour",
        )

    return Space(
        org=org,
        space=space,
        other=other,
        users=users,
        source=source(space, "lai-suat-api", "ingest", "rest_api"),
        federated=source(space, "core-banking", "federated", "postgresql"),
        other_source=source(other, "lai-suat-b", "ingest", "rest_api"),
        dataset=_dataset(seed, org, space, "lai_suat_huy_dong"),
        upload_dataset=_dataset(
            seed,
            org,
            space,
            "du_no_tai_tay",
            origin="upload",
            expires_at="2027-01-01T00:00:00Z",
            display_name="Dư nợ tải tay",
        ),
        other_dataset=_dataset(seed, org, other, "lai_suat_b"),
        seed=seed,
    )


class Tokens:
    def __init__(self, ds: Space) -> None:
        self.ds = ds

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.ds.org)}


def client(ds: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(ds))
    app.dependency_overrides[object_store] = lambda: ds.store
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


JOB = {"name": "Lãi suất huy động", "mode": "full", "source_object": "/api/lai-suat"}


# --- jobs ---------------------------------------------------------------------------------------


async def test_engineer_creates_a_job_on_an_ingest_source(ds: Space) -> None:
    url = f"/v1/sources/{ds.source}/jobs"
    async with client(ds) as api:
        made = await api.post(
            url,
            json={
                **JOB,
                "mode": "incremental",
                "cursor_column": "updated_since",
                "schedule_cron": "*/15 * * * *",
                "target_dataset_id": str(ds.dataset),
            },
            headers=ds.headers("engineer"),
        )
        assert made.status_code == 201, made.text
        job = made.json()
        assert job["status"] == "active" and job["mode"] == "incremental"
        assert job["source_id"] == str(ds.source) and job["data_space_id"] == str(ds.space)
        # BR-56: the job inherits the source's freshness deadline.
        assert job["freshness_sla"] == "PT1H"
        assert job["dlq_retention_days"] == 30
        assert (
            ds.one("SELECT created_by FROM ingest.ingestion_job WHERE id = %s", job["id"])
            == (ds.users["engineer"])
        )
        [(kind, payload)] = ds.events(job["id"])
        assert kind == "ingestion_job.created"
        assert payload["job_id"] == job["id"] and payload["source_id"] == str(ds.source)
        assert payload["data_space_id"] == str(ds.space)

        own_sla = await api.post(
            url,
            json={**JOB, "freshness_sla": "PT30M", "dlq_retention_days": 7},
            headers=ds.headers("engineer"),
        )
        assert own_sla.json()["freshness_sla"] == "PT30M"
        assert own_sla.json()["dlq_retention_days"] == 7


async def test_job_creation_refusals(ds: Space) -> None:
    url = f"/v1/sources/{ds.source}/jobs"
    async with client(ds) as api:
        # A3: an incremental job without a cursor column.
        no_cursor = await api.post(
            url, json={**JOB, "mode": "incremental"}, headers=ds.headers("engineer")
        )
        assert no_cursor.status_code == 422
        assert no_cursor.json()["code"] == "validation_failed"
        # Invariant 15: a federated source is queried in place, never loaded.
        federated = await api.post(
            f"/v1/sources/{ds.federated}/jobs", json=JOB, headers=ds.headers("engineer")
        )
        assert federated.status_code == 422
        assert federated.json()["code"] == "business_rule_violated"
        foreign_target = await api.post(
            url,
            json={**JOB, "target_dataset_id": str(ds.other_dataset)},
            headers=ds.headers("engineer"),
        )
        assert foreign_target.status_code == 422
        # The source of space A does not exist when the engineer works in space B.
        other_space = await api.post(url, json=JOB, headers=ds.headers("engineer", ds.other))
        assert other_space.status_code == 404
        unknown = await api.post(
            f"/v1/sources/{uuid.uuid4()}/jobs", json=JOB, headers=ds.headers("engineer")
        )
        assert unknown.status_code == 404
        no_capability = await api.post(url, json=JOB, headers=ds.headers("analyst"))
        assert no_capability.status_code == 403
        # The analyst is not a member of space B: the header names no Data Space for them.
        not_member = await api.post(url, json=JOB, headers=ds.headers("analyst", ds.other))
        assert not_member.status_code == 404
    assert ds.one("SELECT count(*) FROM ingest.ingestion_job WHERE org_id = %s", ds.org) == 0


# AC-04-09-01
async def test_job_list_carries_the_last_run_lag_and_pending_dlq(ds: Space) -> None:
    healthy = ds.job(name="Tỷ giá")
    failing = ds.job(name="Lãi suất", status="failing", consecutive_failures=2)
    ds.run(healthy, rows_read=10, started_at="2026-10-10T01:00:00Z", status="succeeded")
    latest = ds.run(
        healthy, rows_read=20, lag_seconds=42, started_at="2026-10-11T01:00:00Z", status="running"
    )
    bad_run = ds.run(failing, status="failed", error="connection refused")
    ds.dlq(failing, bad_run)
    ds.dlq(failing, bad_run)
    ds.dlq(failing, bad_run, status="discarded", discard_reason="nguồn sai")
    ds.job(ds.other)
    async with client(ds) as api:
        listed = await api.get("/v1/jobs", headers=ds.headers("engineer"))
        assert listed.status_code == 200, listed.text
        jobs = {j["id"]: j for j in listed.json()["items"]}
        assert set(jobs) == {str(healthy), str(failing)}
        assert jobs[str(healthy)]["last_run"]["id"] == str(latest)
        assert jobs[str(healthy)]["lag_seconds"] == 42
        assert jobs[str(healthy)]["dlq_pending"] == 0
        assert jobs[str(failing)]["dlq_pending"] == 2
        assert jobs[str(failing)]["last_run"]["error"] == "connection refused"

        only_failing = await api.get(
            "/v1/jobs", params={"status": "failing"}, headers=ds.headers("engineer")
        )
        assert [j["id"] for j in only_failing.json()["items"]] == [str(failing)]
        by_source = await api.get(
            "/v1/jobs", params={"source_id": str(ds.federated)}, headers=ds.headers("engineer")
        )
        assert by_source.json()["items"] == []
        paged = await api.get("/v1/jobs", params={"page_size": 1}, headers=ds.headers("engineer"))
        assert len(paged.json()["items"]) == 1 and paged.json()["next_page_token"]
        rest = await api.get(
            "/v1/jobs",
            params={"page_size": 1, "page_token": paged.json()["next_page_token"]},
            headers=ds.headers("engineer"),
        )
        assert {paged.json()["items"][0]["id"], rest.json()["items"][0]["id"]} == set(jobs)

        other = await api.get("/v1/jobs", headers=ds.headers("engineer", ds.other))
        assert len(other.json()["items"]) == 1
        assert str(healthy) not in {j["id"] for j in other.json()["items"]}
        refused = await api.get("/v1/jobs", headers=ds.headers("analyst"))
        assert refused.status_code == 403
        bogus = await api.get(
            "/v1/jobs", params={"status": "sleeping"}, headers=ds.headers("engineer")
        )
        assert bogus.status_code == 422


async def test_engineer_edits_a_job(ds: Space) -> None:
    job = ds.job()
    async with client(ds) as api:
        patched = await api.patch(
            f"/v1/jobs/{job}",
            json={"schedule_cron": "0 2 * * *", "freshness_sla": "P1D", "name": "Lãi suất mới"},
            headers=ds.headers("engineer"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["schedule_cron"] == "0 2 * * *"
        assert patched.json()["freshness_sla"] == "P1D"
        assert ds.one("SELECT name FROM ingest.ingestion_job WHERE id = %s", job) == "Lãi suất mới"
        [(kind, payload)] = ds.events(job)
        assert kind == "ingestion_job.updated"
        assert payload["fields"] == ["freshness_sla", "name", "schedule_cron"]

        cleared = await api.patch(
            f"/v1/jobs/{job}", json={"name": None}, headers=ds.headers("engineer")
        )
        assert cleared.status_code == 422
        other = await api.patch(
            f"/v1/jobs/{job}", json={"name": "x"}, headers=ds.headers("engineer", ds.other)
        )
        assert other.status_code == 404
        refused = await api.patch(
            f"/v1/jobs/{job}", json={"name": "x"}, headers=ds.headers("analyst")
        )
        assert refused.status_code == 403


# AC-04-09-04
async def test_pause_needs_a_reason_and_resume_carries_on(ds: Space) -> None:
    job = ds.job(cursor_value="2026-10-10")
    async with client(ds) as api:
        url = f"/v1/jobs/{job}"
        no_reason = await api.post(f"{url}:pause", json={}, headers=ds.headers("engineer"))
        assert no_reason.status_code == 422
        blank = await api.post(
            f"{url}:pause", json={"reason": "  "}, headers=ds.headers("engineer")
        )
        assert blank.status_code == 422
        assert ds.one("SELECT status FROM ingest.ingestion_job WHERE id = %s", job) == "active"

        paused = await api.post(
            f"{url}:pause", json={"reason": "nguồn bảo trì"}, headers=ds.headers("engineer")
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"
        assert paused.json()["paused_reason"] == "nguồn bảo trì" and paused.json()["paused_at"]
        twice = await api.post(
            f"{url}:pause", json={"reason": "lại"}, headers=ds.headers("engineer")
        )
        assert twice.status_code == 409
        assert twice.json()["code"] == "invalid_state"

        resumed = await api.post(f"{url}:resume", headers=ds.headers("engineer"))
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "active"
        assert resumed.json()["paused_reason"] is None and resumed.json()["paused_at"] is None
        # UC-04-07 step 5: the cursor carries on from where it stopped.
        assert resumed.json()["cursor_value"] == "2026-10-10"
        again = await api.post(f"{url}:resume", headers=ds.headers("engineer"))
        assert again.status_code == 409
        assert [e for e, _ in ds.events(job)] == ["ingestion_job.paused", "ingestion_job.resumed"]

        refused = await api.post(
            f"{url}:pause", json={"reason": "x"}, headers=ds.headers("analyst")
        )
        assert refused.status_code == 403
        other = await api.post(f"{url}:resume", headers=ds.headers("engineer", ds.other))
        assert other.status_code == 404


async def test_a_drifted_job_resumes_only_once_the_drift_is_resolved(ds: Space) -> None:
    drifted = ds.job(status="schema_drift")
    draft = ds.job(status="draft")
    async with client(ds) as api:
        refused = await api.post(f"/v1/jobs/{drifted}:resume", headers=ds.headers("engineer"))
        assert refused.status_code == 409
        assert refused.json()["code"] == "invalid_state"
        not_running = await api.post(
            f"/v1/jobs/{draft}:pause", json={"reason": "x"}, headers=ds.headers("engineer")
        )
        assert not_running.status_code == 409
        # A drifted job can be paused while someone looks at it.
        paused = await api.post(
            f"/v1/jobs/{drifted}:pause",
            json={"reason": "chờ duyệt"},
            headers=ds.headers("engineer"),
        )
        assert paused.status_code == 200


# AC-04-06-02
async def test_backfill_runs_a_range_without_moving_the_cursor(ds: Space) -> None:
    job = ds.job(mode="incremental", cursor_column="updated_since", cursor_value="2026-10-10")
    async with client(ds) as api:
        url = f"/v1/jobs/{job}:backfill"
        accepted = await api.post(
            url, json={"from": "2026-09-01", "to": "2026-09-30"}, headers=ds.headers("engineer")
        )
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()["id"]
        assert accepted.json()["poll_after_ms"] > 0
        assert accepted.headers["Location"] == f"/v1/job-runs?job_id={job}"
        assert ds.one("SELECT cursor_from FROM ingest.job_run WHERE id = %s", run) == "2026-09-01"
        assert ds.one("SELECT cursor_to FROM ingest.job_run WHERE id = %s", run) == "2026-09-30"
        assert (
            ds.one("SELECT cursor_value FROM ingest.ingestion_job WHERE id = %s", job)
            == "2026-10-10"
        )
        [(kind, payload)] = ds.events(run)
        assert kind == "job_run.backfill_requested"
        assert payload["job_id"] == str(job) and payload["cursor_from"] == "2026-09-01"

        tomorrow = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        future = await api.post(
            url, json={"from": "2026-09-01", "to": tomorrow}, headers=ds.headers("engineer")
        )
        assert future.status_code == 422
        assert future.json()["code"] == "business_rule_violated"
        backwards = await api.post(
            url, json={"from": "2026-09-30", "to": "2026-09-01"}, headers=ds.headers("engineer")
        )
        assert backwards.status_code == 422
        draft = ds.job(status="draft")
        refused = await api.post(
            f"/v1/jobs/{draft}:backfill",
            json={"from": "2026-09-01", "to": "2026-09-30"},
            headers=ds.headers("engineer"),
        )
        assert refused.status_code == 409
        other = await api.post(
            url,
            json={"from": "2026-09-01", "to": "2026-09-30"},
            headers=ds.headers("engineer", ds.other),
        )
        assert other.status_code == 404
        forbidden = await api.post(
            url, json={"from": "2026-09-01", "to": "2026-09-30"}, headers=ds.headers("analyst")
        )
        assert forbidden.status_code == 403
    assert ds.one("SELECT count(*) FROM ingest.job_run WHERE job_id = %s", job) == 1


async def test_job_runs_are_listed_per_job_and_data_space(ds: Space) -> None:
    job = ds.job()
    old = ds.run(job, status="succeeded", started_at="2026-10-01T00:00:00Z")
    new = ds.run(job, status="failed", started_at="2026-10-05T00:00:00Z")
    other_run = ds.run(ds.job(ds.other), status="succeeded")
    async with client(ds) as api:
        everything = await api.get("/v1/job-runs", headers=ds.headers("engineer"))
        assert everything.status_code == 200, everything.text
        assert [r["id"] for r in everything.json()["items"]] == [str(new), str(old)]
        failed = await api.get(
            "/v1/job-runs", params={"status": "failed"}, headers=ds.headers("engineer")
        )
        assert [r["id"] for r in failed.json()["items"]] == [str(new)]
        window = await api.get(
            "/v1/job-runs",
            params={"from": "2026-09-30T00:00:00Z", "to": "2026-10-02T00:00:00Z"},
            headers=ds.headers("engineer"),
        )
        assert [r["id"] for r in window.json()["items"]] == [str(old)]
        by_job = await api.get(
            "/v1/job-runs", params={"job_id": str(job)}, headers=ds.headers("engineer", ds.other)
        )
        assert by_job.json()["items"] == []
        other = await api.get("/v1/job-runs", headers=ds.headers("engineer", ds.other))
        assert [r["id"] for r in other.json()["items"]] == [str(other_run)]
        refused = await api.get("/v1/job-runs", headers=ds.headers("analyst"))
        assert refused.status_code == 403


# --- DLQ ----------------------------------------------------------------------------------------


# AC-04-07-03
async def test_dlq_lists_and_replays_a_pending_record_but_not_a_locked_one(ds: Space) -> None:
    job = ds.job(status="schema_drift")
    run = ds.run(job, status="schema_drift", rows_read=500)
    pending = ds.dlq(job, run, error_kind="schema_mismatch", replay_count=1)
    locked = ds.dlq(job, run, status="locked", replay_count=4)
    replayed = ds.dlq(job, run, status="replayed")
    async with client(ds) as api:
        listed = await api.get(f"/v1/jobs/{job}/dlq", headers=ds.headers("engineer"))
        assert listed.status_code == 200, listed.text
        assert {r["id"] for r in listed.json()["items"]} == {
            str(pending),
            str(locked),
            str(replayed),
        }
        mismatch = await api.get(
            f"/v1/jobs/{job}/dlq",
            params={"status": "pending", "error_kind": "schema_mismatch"},
            headers=ds.headers("engineer"),
        )
        assert [r["id"] for r in mismatch.json()["items"]] == [str(pending)]
        hidden = await api.get(f"/v1/jobs/{job}/dlq", headers=ds.headers("engineer", ds.other))
        assert hidden.status_code == 404

        accepted = await api.post(f"/v1/dlq/{pending}:replay", headers=ds.headers("engineer"))
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["id"] == str(pending)
        assert accepted.headers["Location"] == f"/v1/jobs/{job}/dlq"
        [(kind, payload)] = ds.events(pending)
        assert kind == "dlq_record.replay_requested"
        assert payload["job_id"] == str(job) and payload["job_run_id"] == str(run)
        assert payload["replay_count"] == 1
        # The worker does the replay: the api leaves the record as it is.
        assert ds.one("SELECT status FROM ingest.dlq_record WHERE id = %s", pending) == "pending"

        # A3: locked after three failed replays is 409; a replayed record is done.
        for done in (locked, replayed):
            refused = await api.post(f"/v1/dlq/{done}:replay", headers=ds.headers("engineer"))
            assert refused.status_code == 409
            assert refused.json()["code"] == "invalid_state"
        other = await api.post(
            f"/v1/dlq/{pending}:replay", headers=ds.headers("engineer", ds.other)
        )
        assert other.status_code == 404
        forbidden = await api.post(f"/v1/dlq/{pending}:replay", headers=ds.headers("analyst"))
        assert forbidden.status_code == 403


# AC-04-07-02
async def test_discarding_needs_a_reason(ds: Space) -> None:
    job = ds.job()
    run = ds.run(job, rows_read=10_000)
    record = ds.dlq(job, run)
    locked = ds.dlq(job, run, status="locked", replay_count=4)
    async with client(ds) as api:
        for body in ({}, {"reason": " "}):
            refused = await api.post(
                f"/v1/dlq/{record}:discard", json=body, headers=ds.headers("engineer")
            )
            assert refused.status_code == 422
        assert ds.one("SELECT status FROM ingest.dlq_record WHERE id = %s", record) == "pending"

        discarded = await api.post(
            f"/v1/dlq/{record}:discard",
            json={"reason": "dữ liệu nguồn sai, đã báo hệ thống nguồn"},
            headers=ds.headers("engineer"),
        )
        assert discarded.status_code == 200, discarded.text
        assert discarded.json()["status"] == "discarded"
        assert discarded.json()["discard_reason"] == "dữ liệu nguồn sai, đã báo hệ thống nguồn"
        [(kind, payload)] = ds.events(record)
        assert kind == "dlq_record.discarded"
        assert payload["discarded_by"] == str(ds.users["engineer"])
        again = await api.post(
            f"/v1/dlq/{record}:discard", json={"reason": "x"}, headers=ds.headers("engineer")
        )
        assert again.status_code == 409
        # UC-04-07 E1: a locked record is only handled by hand, and discarding is by hand.
        by_hand = await api.post(
            f"/v1/dlq/{locked}:discard", json={"reason": "khoá"}, headers=ds.headers("engineer")
        )
        assert by_hand.status_code == 200
        other = await api.post(
            f"/v1/dlq/{ds.dlq(job, run)}:discard",
            json={"reason": "x"},
            headers=ds.headers("engineer", ds.other),
        )
        assert other.status_code == 404


# AC-04-07-05
async def test_discarding_past_one_percent_of_a_run_needs_the_owner(ds: Space) -> None:
    job = ds.job()
    big = ds.run(job, rows_read=10_000)
    small = ds.run(job, rows_read=10_000)
    for _ in range(100):
        ds.dlq(job, big, status="discarded", discard_reason="nguồn sai")
    for _ in range(79):
        ds.dlq(job, small, status="discarded", discard_reason="nguồn sai")
    over, under = ds.dlq(job, big), ds.dlq(job, small)
    async with client(ds) as api:
        # The 101st of 10,000 is past 1%: the engineer cannot, the Data Space owner can.
        refused = await api.post(
            f"/v1/dlq/{over}:discard", json={"reason": "nguồn sai"}, headers=ds.headers("engineer")
        )
        assert refused.status_code == 422
        assert refused.json()["code"] == "business_rule_violated"
        approved = await api.post(
            f"/v1/dlq/{over}:discard", json={"reason": "nguồn sai"}, headers=ds.headers("owner")
        )
        assert approved.status_code == 200, approved.text
        # The 80th (0.8%) needs nobody.
        within = await api.post(
            f"/v1/dlq/{under}:discard", json={"reason": "nguồn sai"}, headers=ds.headers("engineer")
        )
        assert within.status_code == 200, within.text


# --- uploads ------------------------------------------------------------------------------------


CSV = b"ma_chi_nhanh,du_no_trieu_dong\nCN01,1250.5\nCN02,980\n"


# AC-04-08-01, AC-04-08-03
async def test_upload_keeps_the_original_and_asks_the_worker_to_analyse(ds: Space) -> None:
    async with client(ds) as api:
        made = await api.post(
            "/v1/uploads",
            files={"file": ("du_no_chi_nhanh.csv", CSV, "text/csv")},
            data={"sheet": "Sheet1"},
            headers=ds.headers("uploader"),
        )
        assert made.status_code == 201, made.text
        upload = made.json()
        assert upload["status"] == "uploaded" and upload["size_bytes"] == len(CSV)
        assert upload["file_name"] == "du_no_chi_nhanh.csv" and upload["sheet"] == "Sheet1"
        assert upload["uploader_id"] == str(ds.users["uploader"])
        key = upload["object_key"]
        # The key carries the upload's own id, so an original is written once and never reused.
        assert key == f"{ds.org}/{ds.space}/{upload['id']}.csv"
        assert ds.store.objects[(BUCKET, key)] == (CSV, "text/csv")
        [(kind, payload)] = ds.events(upload["id"])
        assert kind == "file_upload.uploaded"
        assert payload["object_key"] == key and payload["data_space_id"] == str(ds.space)

        # UC-04-08 E1: a workbook with macros is accepted; the reader takes values only.
        macro = await api.post(
            "/v1/uploads",
            files={"file": ("bao_cao.xlsm", b"PK\x03\x04macro", "application/octet-stream")},
            headers=ds.headers("uploader"),
        )
        assert macro.status_code == 201, macro.text
        assert len(ds.store.objects) == 2


async def test_upload_refusals(ds: Space) -> None:
    async with client(ds) as api:
        pdf = await api.post(
            "/v1/uploads",
            files={"file": ("bao_cao.pdf", b"%PDF-1.7", "application/pdf")},
            headers=ds.headers("uploader"),
        )
        assert pdf.status_code == 422
        assert pdf.json()["code"] == "validation_failed"
        empty = await api.post(
            "/v1/uploads",
            files={"file": ("rong.csv", b"", "text/csv")},
            headers=ds.headers("uploader"),
        )
        assert empty.status_code == 422
        missing = await api.post("/v1/uploads", data={"sheet": "x"}, headers=ds.headers("uploader"))
        assert missing.status_code == 422
        forbidden = await api.post(
            "/v1/uploads", files={"file": ("a.csv", CSV, "text/csv")}, headers=ds.headers("analyst")
        )
        assert forbidden.status_code == 403
    assert ds.store.objects == {}
    assert ds.one("SELECT count(*) FROM ingest.file_upload WHERE org_id = %s", ds.org) == 0


# AC-04-08-05
async def test_a_file_over_the_limit_is_refused(ds: Space, monkeypatch: pytest.MonkeyPatch) -> None:
    # The 200 MB check without allocating 200 MB: lower the limit, send one byte more.
    monkeypatch.setattr(ingest_routes, "MAX_UPLOAD_BYTES", 1024)
    async with client(ds) as api:
        too_big = await api.post(
            "/v1/uploads",
            files={"file": ("lon.csv", b"x" * 1025, "text/csv")},
            headers=ds.headers("uploader"),
        )
        assert too_big.status_code == 413
        assert too_big.json()["code"] == "payload_too_large"
        at_limit = await api.post(
            "/v1/uploads",
            files={"file": ("vua.csv", b"x" * 1024, "text/csv")},
            headers=ds.headers("uploader"),
        )
        assert at_limit.status_code == 201
    assert len(ds.store.objects) == 1


async def test_without_object_storage_an_upload_is_unavailable(ds: Space) -> None:
    app = create_app(Settings(), token_verifier=Tokens(ds))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as api:
        refused = await api.post(
            "/v1/uploads",
            files={"file": ("a.csv", CSV, "text/csv")},
            headers=ds.headers("uploader"),
        )
    assert refused.status_code == 503
    assert ds.one("SELECT count(*) FROM ingest.file_upload WHERE org_id = %s", ds.org) == 0


async def test_the_uploader_maps_columns_and_picks_a_manual_table(ds: Space) -> None:
    upload = ds.upload(error_ratio=0.01)
    column_map = [{"source": "du_no_trieu_dong", "target": "du_no", "unit": "triệu đồng"}]
    async with client(ds) as api:
        url = f"/v1/uploads/{upload}"
        mapped = await api.patch(
            url,
            json={"column_map": column_map, "header_row": 2, "decimal_sep": ","},
            headers=ds.headers("uploader"),
        )
        assert mapped.status_code == 200, mapped.text
        assert mapped.json()["status"] == "mapped"
        assert mapped.json()["column_map"] == column_map
        [(kind, payload)] = ds.events(upload)
        assert kind == "file_upload.mapped"
        assert payload["fields"] == ["column_map", "decimal_sep", "header_row"]

        # UC-04-08 4a: only a manual table that already exists takes a merge.
        not_manual = await api.patch(
            url, json={"target_dataset_id": str(ds.dataset)}, headers=ds.headers("uploader")
        )
        assert not_manual.status_code == 422
        foreign = await api.patch(
            url, json={"target_dataset_id": str(ds.other_dataset)}, headers=ds.headers("uploader")
        )
        assert foreign.status_code == 422
        merge = await api.patch(
            url,
            json={"target_dataset_id": str(ds.upload_dataset), "replace_mode": "replace"},
            headers=ds.headers("uploader"),
        )
        assert merge.status_code == 200, merge.text
        assert merge.json()["target_dataset_id"] == str(ds.upload_dataset)
        bad_mode = await api.patch(
            url, json={"replace_mode": "merge"}, headers=ds.headers("uploader")
        )
        assert bad_mode.status_code == 422

        committed = ds.upload(
            status="committed", error_ratio=0.0, target_dataset_id=ds.upload_dataset
        )
        frozen = await api.patch(
            f"/v1/uploads/{committed}", json={"header_row": 1}, headers=ds.headers("uploader")
        )
        assert frozen.status_code == 409
        other = await api.patch(
            url, json={"header_row": 1}, headers=ds.headers("uploader", ds.other)
        )
        assert other.status_code == 404
        forbidden = await api.patch(url, json={"header_row": 1}, headers=ds.headers("analyst"))
        assert forbidden.status_code == 403


# AC-04-08-04
async def test_commit_refuses_more_than_five_percent_of_rows_in_error(ds: Space) -> None:
    bad = ds.upload(error_ratio=0.06)
    good = ds.upload(error_ratio=0.04)
    async with client(ds) as api:
        refused = await api.post(f"/v1/uploads/{bad}:commit", headers=ds.headers("uploader"))
        assert refused.status_code == 422
        assert refused.json()["code"] == "business_rule_violated"
        assert ds.one("SELECT status FROM ingest.file_upload WHERE id = %s", bad) == "analyzed"
        assert ds.events(bad) == []

        written = await api.post(f"/v1/uploads/{good}:commit", headers=ds.headers("uploader"))
        assert written.status_code == 200, written.text
        table = written.json()
        assert table["origin"] == "upload" and table["registered"] is False
        assert table["display_name"] == "du_no.xlsx"
        # BR-105: a manual table expires after 90 days.
        expires = datetime.datetime.fromisoformat(table["expires_at"])
        days = (expires - datetime.datetime.now(datetime.UTC)).days
        assert 89 <= days <= 90
        assert ds.one("SELECT status FROM ingest.file_upload WHERE id = %s", good) == "committed"
        assert (
            str(ds.one("SELECT target_dataset_id FROM ingest.file_upload WHERE id = %s", good))
            == (table["id"])
        )
        [(kind, payload)] = ds.events(good)
        assert kind == "file_upload.committed"
        assert payload["target_dataset_id"] == table["id"] and payload["new_table"] is True

        twice = await api.post(f"/v1/uploads/{good}:commit", headers=ds.headers("uploader"))
        assert twice.status_code == 409
        # The worker has not analysed the file yet.
        fresh = ds.upload(status="uploaded")
        early = await api.post(f"/v1/uploads/{fresh}:commit", headers=ds.headers("uploader"))
        assert early.status_code == 409


# AC-04-08-02
async def test_commit_into_an_existing_manual_table(ds: Space) -> None:
    upload = ds.upload(
        status="mapped",
        error_ratio=0.0,
        target_dataset_id=ds.upload_dataset,
        replace_mode="replace",
    )
    gone = ds.upload(status="mapped", error_ratio=0.0, target_dataset_id=uuid.uuid4())
    async with client(ds) as api:
        other = await api.post(
            f"/v1/uploads/{upload}:commit", headers=ds.headers("uploader", ds.other)
        )
        assert other.status_code == 404
        forbidden = await api.post(f"/v1/uploads/{upload}:commit", headers=ds.headers("analyst"))
        assert forbidden.status_code == 403
        written = await api.post(f"/v1/uploads/{upload}:commit", headers=ds.headers("uploader"))
        assert written.status_code == 200, written.text
        assert written.json()["id"] == str(ds.upload_dataset)
        assert written.json()["registered"] is True
        assert written.json()["table_name"] == "du_no_tai_tay"
        [(_, payload)] = ds.events(upload)
        assert payload["new_table"] is False and payload["replace_mode"] == "replace"
        missing = await api.post(f"/v1/uploads/{gone}:commit", headers=ds.headers("uploader"))
        assert missing.status_code == 422


# --- quality ------------------------------------------------------------------------------------


# AC-06-07-01
async def test_quality_rules_are_declared_on_a_catalogued_table(ds: Space) -> None:
    body = {
        "dataset_id": str(ds.dataset),
        "column_name": "du_no",
        "kind": "not_null",
        "severity": "high",
    }
    async with client(ds) as api:
        made = await api.post("/v1/quality-rules", json=body, headers=ds.headers("engineer"))
        assert made.status_code == 201, made.text
        rule = made.json()
        assert rule["status"] == "active" and rule["origin"] == "manual"
        assert rule["severity"] == "high" and rule["params"] == {}
        [(kind, payload)] = ds.events(rule["id"])
        assert kind == "quality_rule.created" and payload["dataset_id"] == str(ds.dataset)

        default = await api.post(
            "/v1/quality-rules",
            json={"dataset_id": str(ds.dataset), "kind": "range", "params": {"min": 0}},
            headers=ds.headers("engineer"),
        )
        assert default.json()["severity"] == "medium"

        foreign = await api.post(
            "/v1/quality-rules",
            json={**body, "dataset_id": str(ds.other_dataset)},
            headers=ds.headers("engineer"),
        )
        assert foreign.status_code == 422
        bad_kind = await api.post(
            "/v1/quality-rules", json={**body, "kind": "regex"}, headers=ds.headers("engineer")
        )
        assert bad_kind.status_code == 422
        forbidden = await api.post("/v1/quality-rules", json=body, headers=ds.headers("analyst"))
        assert forbidden.status_code == 403

        # Reading the rules needs only `ask`.
        listed = await api.get(
            "/v1/quality-rules", params={"severity": "high"}, headers=ds.headers("analyst")
        )
        assert listed.status_code == 200, listed.text
        assert [r["id"] for r in listed.json()["items"]] == [rule["id"]]
        by_dataset = await api.get(
            "/v1/quality-rules",
            params={"dataset_id": str(ds.dataset)},
            headers=ds.headers("analyst"),
        )
        assert len(by_dataset.json()["items"]) == 2
        other = await api.get("/v1/quality-rules", headers=ds.headers("engineer", ds.other))
        assert other.json()["items"] == []

        patched = await api.patch(
            f"/v1/quality-rules/{rule['id']}",
            json={"severity": "medium", "status": "disabled", "params": {"allow_blank": False}},
            headers=ds.headers("engineer"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["severity"] == "medium" and patched.json()["status"] == "disabled"
        assert ds.events(rule["id"])[-1][0] == "quality_rule.updated"
        assert ds.events(rule["id"])[-1][1]["fields"] == ["params", "severity", "status"]
        hidden = await api.patch(
            f"/v1/quality-rules/{rule['id']}",
            json={"severity": "low"},
            headers=ds.headers("engineer", ds.other),
        )
        assert hidden.status_code == 404
        refused = await api.patch(
            f"/v1/quality-rules/{rule['id']}",
            json={"severity": "low"},
            headers=ds.headers("analyst"),
        )
        assert refused.status_code == 403


# AC-06-07-04
async def test_only_a_failed_high_check_is_acknowledged_by_an_approver(ds: Space) -> None:
    high = ds.seed.row(
        "ingest.quality_rule",
        org_id=ds.org,
        data_space_id=ds.space,
        dataset_id=ds.dataset,
        kind="not_null",
        severity="high",
    )
    medium = ds.seed.row(
        "ingest.quality_rule",
        org_id=ds.org,
        data_space_id=ds.space,
        dataset_id=ds.dataset,
        kind="range",
        severity="medium",
    )
    other_rule = ds.seed.row(
        "ingest.quality_rule",
        org_id=ds.org,
        data_space_id=ds.other,
        dataset_id=ds.other_dataset,
        kind="not_null",
        severity="high",
    )

    def result(rule: uuid.UUID, passed: bool, at: str) -> uuid.UUID:
        return ds.seed.row(
            "ingest.quality_result",
            org_id=ds.org,
            rule_id=rule,
            run_kind="job_run",
            passed=passed,
            violations=0 if passed else 14,
            checked_at=at,
        )

    blocking = result(high, False, "2026-10-11T00:00:00Z")
    fine = result(high, True, "2026-10-10T00:00:00Z")
    labelled = result(medium, False, "2026-10-09T00:00:00Z")
    foreign = result(other_rule, False, "2026-10-11T00:00:00Z")
    async with client(ds) as api:
        listed = await api.get("/v1/quality-results", headers=ds.headers("analyst"))
        assert listed.status_code == 200, listed.text
        assert [r["id"] for r in listed.json()["items"]] == [
            str(blocking),
            str(fine),
            str(labelled),
        ]
        assert listed.json()["items"][0]["severity"] == "high"
        assert listed.json()["items"][0]["dataset_id"] == str(ds.dataset)
        failed = await api.get(
            "/v1/quality-results", params={"passed": "false"}, headers=ds.headers("analyst")
        )
        assert {r["id"] for r in failed.json()["items"]} == {str(blocking), str(labelled)}
        window = await api.get(
            "/v1/quality-results",
            params={"from": "2026-10-10T00:00:00Z", "to": "2026-10-11T00:00:00Z"},
            headers=ds.headers("analyst"),
        )
        assert [r["id"] for r in window.json()["items"]] == [str(fine)]

        url = f"/v1/quality-results/{blocking}:acknowledge"
        no_note = await api.post(url, json={}, headers=ds.headers("owner"))
        assert no_note.status_code == 422
        forbidden = await api.post(url, json={"note": "đã kiểm"}, headers=ds.headers("engineer"))
        assert forbidden.status_code == 403
        done = await api.post(
            url, json={"note": "đã đối chiếu Core, dùng tiếp"}, headers=ds.headers("owner")
        )
        assert done.status_code == 200, done.text
        assert done.json()["acknowledged_by"] == str(ds.users["owner"])
        [(kind, payload)] = ds.events(blocking)
        assert kind == "quality_result.acknowledged"
        assert payload["quality_rule_id"] == str(high)
        twice = await api.post(url, json={"note": "lại"}, headers=ds.headers("owner"))
        assert twice.status_code == 409
        # BR-116: a medium violation only labels the table, nothing to acknowledge.
        for result_id in (labelled, fine):
            refused = await api.post(
                f"/v1/quality-results/{result_id}:acknowledge",
                json={"note": "x"},
                headers=ds.headers("owner"),
            )
            assert refused.status_code == 409
        hidden = await api.post(
            f"/v1/quality-results/{foreign}:acknowledge",
            json={"note": "x"},
            headers=ds.headers("owner"),
        )
        assert hidden.status_code == 404


async def test_upload_reads_stay_in_the_organisation(ds: Space, seed: Seed) -> None:
    """A row of another organisation is invisible even by id (RLS plus the org filter)."""
    stranger = ds.upload(error_ratio=0.0)
    other_org = seed.org()
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest.file_upload SET org_id = %s WHERE id = %s",
            (str(other_org), str(stranger)),
        )
    job = ds.job()
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest.ingestion_job SET org_id = %s WHERE id = %s", (str(other_org), str(job))
        )
    async with client(ds) as api:
        commit = await api.post(f"/v1/uploads/{stranger}:commit", headers=ds.headers("uploader"))
        assert commit.status_code == 404
        pause = await api.post(
            f"/v1/jobs/{job}:pause", json={"reason": "x"}, headers=ds.headers("engineer")
        )
        assert pause.status_code == 404
