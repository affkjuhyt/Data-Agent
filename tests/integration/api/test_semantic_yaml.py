"""The semantic model as YAML, in and out (R1-06, D4) against PostgreSQL as svc_context: a file
imported into one Data Space exports back to the same file, importing it again changes nothing, a
changed formula is a new draft version, and nothing is approved by an import."""

from __future__ import annotations

import pathlib
import uuid
from typing import Any

import pytest
import yaml

from libs.dataagent_db import engine_for, scoped_session
from services.api.kernel.auth import Principal
from services.api.modules.context import semantic_yaml as sy

from .conftest import Seed

pytestmark = [pytest.mark.integration]

MODEL = """
version: 1
semantic_models:
  - name: khoan_vay
    label: Khoản vay
    model: silver.loan
    primary_key: [loan_id]
    dimensions:
      - {name: ngay, label: Ngày, type: time, expr: as_of_date}
      - {name: san_pham, label: Sản phẩm, type: categorical, expr: product_code}
    measures:
      - {name: du_no, label: Dư nợ, agg: sum, expr: balance, additivity: semi, unit: tỷ đồng}
      - name: no_xau
        label: Nợ xấu
        agg: sum
        expr: CASE WHEN group_code >= 3 THEN balance ELSE 0 END
        additivity: semi
      # Two aggregates: no single agg over a column says it, so it is kept whole.
      - {name: du_no_rong, label: Dư nợ ròng, agg: sum, expr: SUM(balance) - SUM(provision)}
  - name: chi_nhanh
    label: Chi nhánh
    model: silver.branch
    primary_key: [code]
    dimensions:
      - {name: vung, label: Vùng, type: categorical, expr: region_name}
relationships:
  - {from: silver.loan, from_columns: [branch_code], to: silver.branch, to_columns: [code],
     cardinality: many_to_one}
metrics:
  - name: NPL_RATIO
    label: Tỷ lệ nợ xấu
    expr: no_xau / du_no * 100
    period_semantics: end_of_period
    time_grain: month
    unit: "%"
    decimals: 2
    direction: lower_better
    dimensions: [san_pham, vung]
    filters: [{dimension: san_pham, op: "!=", values: [NOI_BO]}]
  - name: DU_NO
    label: Dư nợ
    expr: du_no
    period_semantics: end_of_period
    unit: tỷ đồng
"""


class Space:
    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.org = seed.org()
        self.user = seed.row(
            "org.app_user",
            org_id=self.org,
            external_id="kc-steward",
            username="steward",
            display_name="Steward",
            status="active",
        )
        self.id = seed.row(
            "org.data_space", org_id=self.org, code="DS-TD", name="Tín dụng", steward_id=self.user
        )
        for table in ("loan", "branch"):
            seed.row(
                "catalog.dataset",
                org_id=self.org,
                data_space_id=self.id,
                origin="derived",
                schema_name="silver",
                table_name=table,
                enabled=True,
            )
        self.principal = Principal(
            user_id=self.user, org_id=self.org, external_id="kc-steward", data_space_id=self.id
        )

    async def load(self, text: str) -> sy.ImportReport:
        async with scoped_session("svc_context", self.org, self.user) as session:
            return await sy.import_model(session, self.principal, self.id, sy.parse(text))

    async def export(self) -> dict[str, Any]:
        async with scoped_session("svc_context", self.org, self.user) as session:
            return await sy.export_model(session, self.id)

    def all(self, sql: str) -> list[tuple[Any, ...]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, (str(self.id),))
            return list(cur.fetchall())

    def approve_all(self) -> None:
        # What review does (UC-07-03); the import itself never does it.
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE context.relationship SET status = 'active', updated_by = %s"
                " WHERE data_space_id = %s",
                (str(self.user), str(self.id)),
            )


async def test_a_model_imports_unapproved_and_exports_back_the_same(seed: Seed) -> None:
    space = Space(seed)
    report = await space.load(MODEL)
    assert report.created == {
        "semantic_model": 2,
        "dimension": 3,
        "measure": 3,
        "relationship": 1,
        "metric": 2,
    }
    # Nothing is approved by an import: review (UC-07-03) acts on the events.
    assert space.all("SELECT status FROM context.relationship WHERE data_space_id = %s") == [
        ("pending_review",)
    ]
    assert sorted(
        space.all(
            "SELECT m.code, m.status, v.version, v.status FROM context.metric m JOIN"
            " context.metric_version v ON v.metric_id = m.id WHERE m.data_space_id = %s"
        )
    ) == [("DU_NO", "draft", 1, "draft"), ("NPL_RATIO", "draft", 1, "draft")]
    events = [
        e
        for (e,) in space.all(
            "SELECT o.event_type FROM context.outbox o JOIN context.metric m ON m.id ="
            " o.aggregate_id WHERE m.data_space_id = %s"
        )
    ]
    assert sorted(events) == ["metric.created", "metric.created"]
    # A measure written as agg over a column is stored aggregated, and exported back as written.
    assert space.all(
        "SELECT expression FROM context.semantic_measure WHERE data_space_id = %s AND code = 'du_no'"
    ) == [("SUM(balance)",)]
    assert space.all(
        "SELECT expression FROM context.semantic_measure WHERE data_space_id = %s"
        " AND code IN ('no_xau', 'du_no_rong') ORDER BY code"
    ) == [
        ("SUM(balance) - SUM(provision)",),
        ("SUM(CASE WHEN group_code >= 3 THEN balance ELSE 0 END)",),
    ]

    space.approve_all()
    exported = await space.export()
    wanted = yaml.safe_load(MODEL)
    for metric in wanted["metrics"]:
        metric.setdefault("time_grain", "day")
        metric.setdefault("decimals", 2)
        metric.setdefault("direction", "neutral")
        metric.setdefault("additivity", "none")
        metric.setdefault("filters", [])
        metric["dimensions"] = sorted(metric.get("dimensions", []))
    exported_metrics = {m["name"]: m for m in exported["metrics"]}
    for metric in wanted["metrics"]:
        got = exported_metrics[metric["name"]]
        assert {k: got.get(k) for k in metric} == metric
    # Exported in code order, defaults written out; otherwise each model as the file wrote it.
    for model in wanted["semantic_models"]:
        model.setdefault("measures", [])
        for measure in model["measures"]:
            measure.setdefault("additivity", "full")
        model["measures"].sort(key=lambda m: m["name"])
        model["dimensions"].sort(key=lambda d: d["name"])
    by_name = {m["name"]: m for m in exported["semantic_models"]}
    assert [by_name[m["name"]] for m in wanted["semantic_models"]] == wanted["semantic_models"]
    assert exported["relationships"] == wanted["relationships"]


async def test_importing_again_changes_nothing_and_a_new_formula_is_a_draft_version(
    seed: Seed,
) -> None:
    space = Space(seed)
    await space.load(MODEL)
    again = await space.load(MODEL)
    assert again.created == {} and again.updated == {}
    assert again.unchanged["metric"] == 2 and again.unchanged["relationship"] == 1

    changed = MODEL.replace("expr: no_xau / du_no * 100", "expr: no_xau / du_no * 1000")
    report = await space.load(changed)
    assert report.updated == {"metric": 1}
    assert sorted(
        space.all(
            "SELECT v.version, v.status, v.expression FROM context.metric_version v JOIN"
            " context.metric m ON m.id = v.metric_id WHERE m.data_space_id = %s"
            " AND m.code = 'NPL_RATIO'"
        )
    ) == [(1, "draft", "no_xau / du_no * 100"), (2, "draft", "no_xau / du_no * 1000")]


async def test_a_file_that_does_not_resolve_writes_nothing(seed: Seed) -> None:
    space = Space(seed)
    broken = MODEL.replace("model: silver.branch", "model: silver.khong_co").replace(
        "expr: du_no\n", "expr: du_no + phi\n"
    )
    with pytest.raises(sy.ImportProblem) as caught:
        await space.load(broken)
    assert any("silver.khong_co" in p for p in caught.value.problems)
    assert any("phi" in p for p in caught.value.problems)
    assert space.all("SELECT count(*) FROM context.semantic_entity WHERE data_space_id = %s") == [
        (0,)
    ]


async def test_a_pack_metric_is_left_as_it_is(seed: Seed) -> None:
    space = Space(seed)
    await space.load(MODEL)
    with space.seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE context.metric SET pack_metric_id = %s WHERE data_space_id = %s"
            " AND code = 'DU_NO'",
            (str(uuid.uuid4()), str(space.id)),
        )
    report = await space.load(MODEL.replace("label: Dư nợ\n    expr", "label: Dư nợ mới\n    expr"))
    assert report.skipped == ["DU_NO: chỉ số của gói ngành, công thức bị khoá"]


def test_the_command_line_exports_and_dry_runs(
    seed: Seed, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    space = Space(seed)
    path = tmp_path / "model.yaml"
    path.write_text(MODEL, encoding="utf-8")
    args = ["--org", str(space.org), "--space", "DS-TD"]

    def main(argv: list[str]) -> int:
        # Each run is a process of its own in use: its own event loop, so no pooled connection
        # of another loop.
        engine_for.cache_clear()
        try:
            return sy.main(argv)
        finally:
            engine_for.cache_clear()

    assert main(["import", str(path), *args, "--as", "steward", "--dry-run"]) == 0
    out = yaml.safe_load(capsys.readouterr().out)
    assert out["dry_run"] is True and out["created"]["metric"] == 2
    assert space.all("SELECT count(*) FROM context.metric WHERE data_space_id = %s") == [(0,)]
    assert main(["import", str(path), *args, "--as", "steward"]) == 0
    capsys.readouterr()
    assert main(["export", *args]) == 0
    exported = yaml.safe_load(capsys.readouterr().out)
    assert [m["name"] for m in exported["metrics"]] == ["DU_NO", "NPL_RATIO"]


async def test_the_pilot_model_imports_whole(seed: Seed) -> None:
    # deploy/docker/semantic/ds-bh.yaml, the R1-06 pilot, on the pilot's catalogued tables.
    space = Space(seed)
    for table in (
        "pilot_sales_line",
        "pilot_inventory",
        "pilot_receivable",
        "pilot_period_edition",
    ):
        seed.row(
            "catalog.dataset",
            org_id=space.org,
            data_space_id=space.id,
            origin="derived",
            schema_name="workspace",
            table_name=table,
            enabled=True,
        )
    text = (pathlib.Path(__file__).parents[3] / "deploy/docker/semantic/ds-bh.yaml").read_text(
        encoding="utf-8"
    )
    report = await space.load(text)
    assert report.created["semantic_model"] == 4 and report.created["metric"] == 20
    exported = await space.export()
    assert sorted(m["name"] for m in exported["metrics"]) == sorted(
        m["name"] for m in yaml.safe_load(text)["metrics"]
    )
