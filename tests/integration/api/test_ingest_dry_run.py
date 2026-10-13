"""POST /v1/sources/{id}/jobs?dry_run=true (UC-04-06, added 12/10/2026) against PostgreSQL.

A dry run checks the job exactly as a create does and records nothing: no job row, no outbox
event. The 1,000-row trial on the source is worker-ingest's, so the answer says so instead of
carrying a sample. Organisation, users and sources come from the ingest suite's fixture.
"""

from __future__ import annotations

import uuid

import pytest

from . import test_ingest
from .test_ingest import JOB, Space, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]
# The ingest suite's fixture, reused: the same organisation, users and rows.
ds = test_ingest.ds


def _jobs(ds: Space) -> int:
    return int(ds.one("SELECT count(*) FROM ingest.ingestion_job WHERE org_id = %s", ds.org))


def _events(ds: Space) -> int:
    return int(ds.one("SELECT count(*) FROM ingest.outbox WHERE org_id = %s", ds.org))


async def test_dry_run_answers_the_would_be_job_and_records_nothing(ds: Space) -> None:
    url = f"/v1/sources/{ds.source}/jobs"
    body = {
        **JOB,
        "mode": "incremental",
        "cursor_column": "updated_since",
        "target_dataset_id": str(ds.dataset),
    }
    async with client(ds) as api:
        tried = await api.post(
            url, params={"dry_run": "true"}, json=body, headers=ds.headers("engineer")
        )
        assert tried.status_code == 200, tried.text
        job = tried.json()
        assert job["dry_run"] is True
        assert job["mode"] == "incremental" and job["cursor_column"] == "updated_since"
        assert job["source_id"] == str(ds.source) and job["data_space_id"] == str(ds.space)
        # The table's defaults apply as on a create, and BR-56 inherits the source's deadline.
        assert job["status"] == "active" and job["dlq_retention_days"] == 30
        assert job["freshness_sla"] == "PT1H"
        # The sample is worker-ingest's; the api says so rather than inventing rows.
        assert job["sample"] == [] and job["errors"] == []
        assert "worker-ingest" in job["note"]
        assert _jobs(ds) == 0 and _events(ds) == 0
        assert ds.events(job["id"]) == []

        # The same body without dry_run creates the job, as before.
        made = await api.post(url, json=body, headers=ds.headers("engineer"))
        assert made.status_code == 201, made.text
        assert "dry_run" not in made.json() and "note" not in made.json()
        assert _jobs(ds) == 1


async def test_dry_run_refuses_what_create_refuses(ds: Space) -> None:
    url = f"/v1/sources/{ds.source}/jobs"
    dry = {"dry_run": "true"}
    async with client(ds) as api:
        no_cursor = await api.post(
            url, params=dry, json={**JOB, "mode": "incremental"}, headers=ds.headers("engineer")
        )
        assert no_cursor.status_code == 422
        assert no_cursor.json()["code"] == "validation_failed"
        federated = await api.post(
            f"/v1/sources/{ds.federated}/jobs",
            params=dry,
            json=JOB,
            headers=ds.headers("engineer"),
        )
        assert federated.status_code == 422
        assert federated.json()["code"] == "business_rule_violated"
        foreign_target = await api.post(
            url,
            params=dry,
            json={**JOB, "target_dataset_id": str(ds.other_dataset)},
            headers=ds.headers("engineer"),
        )
        assert foreign_target.status_code == 422
        # Capability gate: admin_data, as for a create.
        analyst = await api.post(url, params=dry, json=JOB, headers=ds.headers("analyst"))
        assert analyst.status_code == 403
        # Data Space isolation: the source of space A is not found from space B.
        other_space = await api.post(
            url, params=dry, json=JOB, headers=ds.headers("engineer", ds.other)
        )
        assert other_space.status_code == 404
        unknown = await api.post(
            f"/v1/sources/{uuid.uuid4()}/jobs",
            params=dry,
            json=JOB,
            headers=ds.headers("engineer"),
        )
        assert unknown.status_code == 404
    assert _jobs(ds) == 0 and _events(ds) == 0
