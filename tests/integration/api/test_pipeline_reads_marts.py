"""The pipeline-module endpoints added 12/10/2026 against PostgreSQL: GET /v1/pipelines/{id},
GET /v1/writebacks/{id}, GET /v1/marts, GET /v1/marts/{id} and PATCH /v1/marts/{id}
(UC-06-02 to UC-06-04, UC-06-06).

Organisation, users, sources and datasets come from the pipeline suite's fixture. The pipeline
worker is not running: rows it would write (runs, diffs, a passed relation check) are seeded.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from . import test_pipeline
from .test_pipeline import STEPS, Space, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]
# The pipeline suite's fixture, reused: the same organisation, users and rows.
pp = test_pipeline.pp


def _mart(pp: Space, space: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
    values.setdefault("status", "draft")
    return pp.seed.row(
        "pipeline.data_mart",
        org_id=pp.org,
        data_space_id=space or pp.space,
        name=values.pop("name", "fact_loan_daily"),
        fact_dataset_id=pp.derived if space in (None, pp.space) else pp.other_dataset,
        grain="khoản vay theo ngày",
        measures=Json([{"name": "du_no", "agg": "sum"}]),
        dimensions=Json([{"name": "chi_nhanh", "key": "ma_chi_nhanh"}]),
        relations=Json([{"from": "ma_chi_nhanh", "to": "dim_chi_nhanh.ma"}]),
        **values,
    )


# --- GET /v1/pipelines/{id} ---------------------------------------------------------------------


async def test_a_pipeline_reads_with_its_latest_run(pp: Space) -> None:
    async with client(pp) as api:
        made = await api.post(
            "/v1/pipelines",
            json={"name": "Làm sạch dư nợ", "input_dataset_ids": [str(pp.raw)], "steps": STEPS},
            headers=pp.headers("eng"),
        )
        assert made.status_code == 201, made.text
        pipeline = made.json()["id"]
        url = f"/v1/pipelines/{pipeline}"

        fresh = await api.get(url, headers=pp.headers("eng"))
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["last_run"] is None
        assert fresh.json()["steps"][0]["kind"] == "filter" and fresh.json()["cron"] is None

        pp.seed.row(
            "pipeline.pipeline_run",
            org_id=pp.org,
            pipeline_id=pipeline,
            mode="preview",
            status="succeeded",
            started_at="2026-10-10T08:00:00Z",
        )
        latest = pp.seed.row(
            "pipeline.pipeline_run",
            org_id=pp.org,
            pipeline_id=pipeline,
            mode="full",
            status="failed",
            error="mất kết nối",
            started_at="2026-10-11T08:00:00Z",
        )
        read = await api.get(url, headers=pp.headers("eng"))
        last = read.json()["last_run"]
        assert last["id"] == str(latest) and last["status"] == "failed" and last["mode"] == "full"

        # The ETag is the one PATCH works with, so a read-then-write passes If-Match.
        patched = await api.patch(
            url,
            json={"name": "Làm sạch dư nợ v2"},
            headers={**pp.headers("eng"), "If-Match": read.headers["ETag"]},
        )
        assert patched.status_code == 200, patched.text
        stale = await api.patch(
            url,
            json={"name": "Cũ"},
            headers={**pp.headers("eng"), "If-Match": read.headers["ETag"]},
        )
        assert stale.status_code == 412, stale.text
        again = await api.get(url, headers=pp.headers("eng"))
        assert again.headers["ETag"] == patched.headers["ETag"]


async def test_a_pipeline_read_is_visible_scoped_and_gated(pp: Space) -> None:
    async with client(pp) as api:
        made = await api.post(
            "/v1/pipelines",
            json={"name": "Của riêng", "input_dataset_ids": [str(pp.raw)], "steps": STEPS},
            headers=pp.headers("eng"),
        )
        url = f"/v1/pipelines/{made.json()['id']}"
        # BR-111: a personal pipeline is its owner's.
        assert (await api.get(url, headers=pp.headers("eng2"))).status_code == 404
        assert (await api.get(url, headers=pp.headers("eng", pp.other))).status_code == 404
        unknown = await api.get(f"/v1/pipelines/{uuid.uuid4()}", headers=pp.headers("eng"))
        assert unknown.status_code == 404
        assert (await api.get(url, headers=pp.headers("nobody"))).status_code == 403
        shared = await api.post(
            "/v1/pipelines",
            json={
                "name": "Dùng chung",
                "input_dataset_ids": [str(pp.raw)],
                "steps": STEPS,
                "scope": "data_space",
            },
            headers=pp.headers("eng"),
        )
        analyst = await api.get(
            f"/v1/pipelines/{shared.json()['id']}", headers=pp.headers("analyst")
        )
        assert analyst.status_code == 200, analyst.text


# --- GET /v1/writebacks/{id} --------------------------------------------------------------------


def _column(pp: Space, dataset: uuid.UUID, name: str, ordinal: int, **values: Any) -> None:
    pp.seed.row(
        "catalog.column_meta",
        org_id=pp.org,
        dataset_id=dataset,
        dataset_version=1,
        name=name,
        ordinal=ordinal,
        data_type="text",
        normalized_type="string",
        **values,
    )


async def test_a_writeback_reads_without_sensitive_columns_in_its_sample(pp: Space) -> None:
    # Personal data on the table the write reads from, and on the source's target table.
    _column(pp, pp.derived, "ma_khoan_vay", 1)
    _column(pp, pp.derived, "SO_CCCD", 2, pii_label="national_id")
    target = pp.seed.row(
        "catalog.dataset",
        org_id=pp.org,
        data_space_id=pp.space,
        source_id=pp.source,
        origin="source",
        schema_name="core",
        table_name="du_no_dieu_chinh",
        enabled=True,
    )
    _column(pp, target, "so_dien_thoai", 1, pii_label="phone")
    # A column that no longer exists keeps no label in force.
    _column(pp, target, "ghi_chu", 2, pii_label="other", removed_in_version=2)
    async with client(pp) as api:
        made = await api.post(
            "/v1/writebacks",
            json={
                "source_id": str(pp.source),
                "target_table": "core.du_no_dieu_chinh",
                "from_dataset_id": str(pp.derived),
            },
            headers=pp.headers("eng"),
        )
        assert made.status_code == 201, made.text
        writeback = made.json()["id"]
        pp.run(
            "UPDATE pipeline.writeback_request SET status = 'previewed', diff_summary = %s"
            " WHERE id = %s",
            Json(
                {
                    "inserts": 1,
                    "updates": 1,
                    "deletes": 0,
                    "sample": [
                        {
                            "before": {"ma_khoan_vay": "KV1", "so_cccd": "0123", "du_no": 100},
                            "after": {"ma_khoan_vay": "KV1", "so_cccd": "0123", "du_no": 120},
                        }
                    ],
                    "columns": [
                        {"column": "so_dien_thoai", "before": "090", "after": "091"},
                        {"column": "du_no", "before": 100, "after": 120},
                        {"column": "ghi_chu", "before": "a", "after": "b"},
                    ],
                }
            ),
            writeback,
        )
        url = f"/v1/writebacks/{writeback}"
        read = await api.get(url, headers=pp.headers("eng"))
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["status"] == "previewed" and body["target_table"] == "core.du_no_dieu_chinh"
        summary = body["diff_summary"]
        assert summary["inserts"] == 1 and summary["deletes"] == 0
        assert summary["sample"] == [
            {
                "before": {"ma_khoan_vay": "KV1", "du_no": 100},
                "after": {"ma_khoan_vay": "KV1", "du_no": 120},
            }
        ]
        assert [c["column"] for c in summary["columns"]] == ["du_no", "ghi_chu"]
        assert "0123" not in read.text and "090" not in read.text

        # :undo's Location is this read.
        pp.run(
            "UPDATE pipeline.writeback_request SET status = 'executed', executed_at = now(),"
            " approved_by = %s WHERE id = %s",
            pp.users["approver"],
            writeback,
        )
        undo = await api.post(f"{url}:undo", json={"reason": "Sai kỳ"}, headers=pp.headers("eng"))
        assert undo.status_code == 202, undo.text
        followed = await api.get(undo.headers["Location"], headers=pp.headers("eng"))
        assert followed.json()["status"] == "undo_requested"

        # Capability writeback: an analyst who only asks does not read writebacks.
        assert (await api.get(url, headers=pp.headers("analyst"))).status_code == 403
        assert (await api.get(url, headers=pp.headers("eng", pp.other))).status_code == 404
        unknown = await api.get(f"/v1/writebacks/{uuid.uuid4()}", headers=pp.headers("eng"))
        assert unknown.status_code == 404


# --- data marts ---------------------------------------------------------------------------------


async def test_marts_list_and_read(pp: Space) -> None:
    draft = _mart(pp, name="fact_du_no")
    active = _mart(
        pp,
        name="fact_tien_gui",
        status="active",
        relation_check_passed=True,
        suggested_dimensions=Json([{"name": "ky_han", "key": "ma_ky_han"}]),
    )
    retired = _mart(pp, name="fact_cu", status="retired")
    elsewhere = _mart(pp, pp.other, name="fact_ke_toan")
    async with client(pp) as api:
        listed = await api.get("/v1/marts", headers=pp.headers("analyst"))
        assert listed.status_code == 200, listed.text
        assert {r["id"] for r in listed.json()["items"]} == {str(draft), str(active), str(retired)}
        actives = await api.get(
            "/v1/marts", params={"status": "active"}, headers=pp.headers("analyst")
        )
        assert [r["id"] for r in actives.json()["items"]] == [str(active)]
        by_name = await api.get(
            "/v1/marts", params={"order_by": "name asc", "page_size": 2}, headers=pp.headers("eng")
        )
        assert [r["name"] for r in by_name.json()["items"]] == ["fact_cu", "fact_du_no"]
        assert by_name.json()["next_page_token"]
        there = await api.get("/v1/marts", headers=pp.headers("eng", pp.other))
        assert [r["id"] for r in there.json()["items"]] == [str(elsewhere)]

        one = await api.get(f"/v1/marts/{active}", headers=pp.headers("analyst"))
        assert one.status_code == 200, one.text
        assert one.json()["relation_check_passed"] is True
        assert one.json()["dimensions"] == [{"name": "chi_nhanh", "key": "ma_chi_nhanh"}]
        assert one.json()["suggested_dimensions"] == [{"name": "ky_han", "key": "ma_ky_han"}]
        assert by_name.json()["items"][1]["suggested_dimensions"] is None
        foreign = await api.get(f"/v1/marts/{elsewhere}", headers=pp.headers("eng"))
        assert foreign.status_code == 404
        unknown = await api.get(f"/v1/marts/{uuid.uuid4()}", headers=pp.headers("analyst"))
        assert unknown.status_code == 404
        assert (await api.get("/v1/marts", headers=pp.headers("nobody"))).status_code == 403
        gated = await api.get(f"/v1/marts/{draft}", headers=pp.headers("nobody"))
        assert gated.status_code == 403


async def test_patching_a_mart_reruns_its_relation_check(pp: Space) -> None:
    mart = _mart(pp, status="active", relation_check_passed=True)
    url = f"/v1/marts/{mart}"
    dimensions = [
        {"name": "chi_nhanh", "key": "ma_chi_nhanh"},
        # Accepting the agent's suggested dimension: the current list plus that one.
        {"name": "ky_han", "key": "ma_ky_han", "suggested_by": "agent"},
    ]
    async with client(pp) as api:
        renamed = await api.patch(
            url, json={"name": "fact_du_no_ngay"}, headers=pp.headers("dataadmin")
        )
        assert renamed.status_code == 200, renamed.text
        # A name says nothing about relations: the passed check stands.
        assert renamed.json()["relation_check_passed"] is True
        [(kind, payload)] = pp.events(mart)
        assert kind == "data_mart.updated" and payload["relation_check_requested"] is False

        read = await api.get(url, headers=pp.headers("dataadmin"))
        added = await api.patch(
            url,
            json={
                "dimensions": dimensions,
                "relations": [{"from": "ma_ky_han", "to": "dim_ky_han.ma"}],
            },
            headers={**pp.headers("dataadmin"), "If-Match": read.headers["ETag"]},
        )
        assert added.status_code == 200, added.text
        body = added.json()
        assert body["dimensions"] == dimensions and body["name"] == "fact_du_no_ngay"
        assert body["measures"] == [{"name": "du_no", "agg": "sum"}]
        # An active mart needs a passed check: it is a draft until the worker's check passes.
        assert body["relation_check_passed"] is False and body["status"] == "draft"
        assert (
            pp.one("SELECT relation_check_passed FROM pipeline.data_mart WHERE id = %s", mart)
            is False
        )
        _, payload = pp.events(mart)[-1]
        assert payload["relation_check_requested"] is True
        assert payload["previous_status"] == "active" and payload["status"] == "draft"
        assert payload["changed"] == ["dimensions", "relations"]
        assert payload["data_space_id"] == str(pp.space)

        stale = await api.patch(
            url,
            json={"name": "khác"},
            headers={**pp.headers("dataadmin"), "If-Match": read.headers["ETag"]},
        )
        assert stale.status_code == 412


async def test_mart_patch_refusals(pp: Space) -> None:
    mart = _mart(pp)
    retired = _mart(pp, name="fact_cu", status="retired")
    elsewhere = _mart(pp, pp.other, name="fact_ke_toan")
    url = f"/v1/marts/{mart}"
    async with client(pp) as api:
        gone = await api.patch(
            f"/v1/marts/{retired}", json={"name": "x"}, headers=pp.headers("dataadmin")
        )
        assert gone.status_code == 409 and gone.json()["code"] == "invalid_state"
        for body in (
            {},
            {"dimensions": None},
            {"name": "  "},
            {"grain": "khác"},
            {"measures": "x"},
        ):
            refused = await api.patch(url, json=body, headers=pp.headers("dataadmin"))
            assert refused.status_code == 422, (body, refused.text)
            assert refused.json()["code"] == "validation_failed"
        # admin_data edits a mart; the engineer only reads it.
        assert (
            await api.patch(url, json={"name": "x"}, headers=pp.headers("eng"))
        ).status_code == 403
        foreign = await api.patch(
            f"/v1/marts/{elsewhere}", json={"name": "x"}, headers=pp.headers("dataadmin")
        )
        assert foreign.status_code == 404
        unknown = await api.patch(
            f"/v1/marts/{uuid.uuid4()}", json={"name": "x"}, headers=pp.headers("dataadmin")
        )
        assert unknown.status_code == 404
    assert pp.events(mart) == [] and pp.events(retired) == []
