"""worker-agent writes an audit export (UC-11-03 step 4, BR-138): the organisation's log rows the
job's filters matched up to the seq counted at request, signed, stored in da-exports, the job
`ready`; an approved large export is written by its approval."""

from __future__ import annotations

import csv
import datetime
import io
import json
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg2
import pytest

from services.worker_agent import audit_exports, storage
from services.worker_agent.main import HANDLERS, run_once, sources

from .conftest import Seed

pytestmark = [pytest.mark.integration]

KEY = "audit-export-test-key"


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        self.objects[(bucket, key)] = (body, content_type)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(storage, "_store", memory)
    monkeypatch.setenv(audit_exports.KEY_ENV, KEY)
    return memory


class AuditDb:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def log(self, org: uuid.UUID, action: str, after: dict[str, Any] | None = None) -> int:
        with self.conn.cursor() as cur:
            # seq, prev_hash and hash are the chain trigger's.
            cur.execute(
                "INSERT INTO audit.audit_log (id, org_id, seq, actor_kind, service, action, after,"
                " prev_hash, hash) VALUES (%s, %s, 0, 'user', 'api', %s, %s, '', '') RETURNING seq",
                (str(uuid.uuid4()), str(org), action, json.dumps(after) if after else None),
            )
            return int(cur.fetchone()[0])  # type: ignore[index]

    def job(self, org: uuid.UUID, filters: dict[str, Any], **values: Any) -> uuid.UUID:
        job = uuid.uuid4()
        values = {"format": "csv", "status": "queued", "row_count": 2, **values}
        cols = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO audit.export_job (id, org_id, filters, requested_by, {cols})"
                f" VALUES (%s, %s, %s, %s, {marks})",
                (str(job), str(org), json.dumps(filters), str(uuid.uuid4()), *values.values()),
            )
        return job

    def requested(self, org: uuid.UUID, job: uuid.UUID) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit.outbox (id, org_id, aggregate_type, aggregate_id, event_type,"
                " payload) VALUES (%s, %s, 'export_job', %s, 'audit.export_requested', %s)",
                (str(uuid.uuid4()), str(org), str(job), json.dumps({"export_job_id": str(job)})),
            )

    def state(self, job: uuid.UUID) -> tuple[Any, ...]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status, object_key, signature, expires_at FROM audit.export_job"
                " WHERE id = %s",
                (str(job),),
            )
            return cur.fetchone()  # type: ignore[return-value]


@pytest.fixture
def audit_db(ddl_server: dict[str, str]) -> Iterator[AuditDb]:
    db = AuditDb(ddl_server["dataagent_audit"])
    yield db
    db.conn.close()


def _org(seed: Seed) -> uuid.UUID:
    return seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")


async def _drain() -> None:
    for source in await sources():
        while await run_once(source, HANDLERS, batch=50):
            pass


async def test_a_queued_export_is_written_signed_and_ready(
    seed: Seed, audit_db: AuditDb, store: MemoryStore
) -> None:
    org, other = _org(seed), _org(seed)
    audit_db.log(org, "query.executed", {"sql": '=HYPERLINK("x")'})
    audit_db.log(org, "source.created")
    last = audit_db.log(org, "query.executed")
    audit_db.log(other, "query.executed")
    # Logged after the request: past the seq it counted, so not in the file.
    audit_db.log(org, "query.executed")
    job = audit_db.job(org, {"action": "query.executed", "to_seq": last})
    audit_db.requested(org, job)
    await _drain()
    await _drain()  # delivered again: nothing changes
    status, key, signature, expires_at = audit_db.state(job)
    assert (status, key) == ("ready", f"{org}/audit/{job}.csv")
    assert expires_at > datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=23)
    body, content_type = store.objects[("da-exports", key)]
    assert content_type.startswith("text/csv")
    assert audit_exports.verify(org, body, signature, KEY.encode())
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert [(r["seq"], r["action"]) for r in rows] == [
        ("1", "query.executed"),
        (str(last), "query.executed"),
    ]
    # The chain's own fields are in the file, so it can be checked against the log.
    assert all(len(r["hash"]) == 64 and len(r["prev_hash"]) == 64 for r in rows)
    assert json.loads(rows[0]["after"]) == {"sql": '=HYPERLINK("x")'}


async def test_a_jsonl_export_and_one_without_a_key(
    seed: Seed, audit_db: AuditDb, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = _org(seed)
    last = audit_db.log(org, "query.executed")
    jsonl = audit_db.job(org, {"to_seq": last}, format="jsonl", row_count=1)
    audit_db.requested(org, jsonl)
    await _drain()
    status, key, signature, _ = audit_db.state(jsonl)
    body, _ = store.objects[("da-exports", key)]
    assert status == "ready" and [json.loads(x)["seq"] for x in body.splitlines()] == [last]
    assert audit_exports.verify(org, body, signature, KEY.encode())

    monkeypatch.delenv(audit_exports.KEY_ENV)
    unsigned = audit_db.job(org, {"to_seq": last}, row_count=1)
    audit_db.requested(org, unsigned)
    await _drain()
    # No file leaves without its signature.
    assert audit_db.state(unsigned) == ("failed", None, None, None)


async def test_an_export_still_waiting_for_approval_is_not_written(
    seed: Seed, audit_db: AuditDb, store: MemoryStore
) -> None:
    org = _org(seed)
    last = audit_db.log(org, "query.executed")
    job = audit_db.job(org, {"to_seq": last}, status="waiting_approval", row_count=200000)
    audit_db.requested(org, job)
    await _drain()
    assert audit_db.state(job)[0] == "waiting_approval" and store.objects == {}
