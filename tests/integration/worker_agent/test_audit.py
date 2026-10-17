"""A data-quality agent audits the table its goal names (worker-agent audit/, runs.py): the latest
snapshot profiled in place, the gaps told apart from what does not apply, where groups
concentrate, the key checked; read-only queries recorded as the run's."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

from .conftest import Seed
from .test_answers import (
    Bank,
    build,
    warehouse,  # noqa: F401  (the fixture `credit` reads)
)
from .test_runs import agent_run, drain, run_row, steps

pytestmark = [pytest.mark.integration]

ROWS = (
    # 30/09: ten individuals (one without gender), five SME loans with no gender or age (they do
    # not apply), and L15 twice.
    [(f"L0{i}", "Cá nhân", "Nam", "26-35", 100, "2026-09-30") for i in range(1, 5)]
    + [("L05", "Cá nhân", "Nam", "36-45", 120, "2026-09-30")]
    + [(f"L0{i}", "Cá nhân", "Nữ", "36-45", 90, "2026-09-30") for i in range(6, 9)]
    + [("L09", "Cá nhân", "Nữ", "26-35", 80, "2026-09-30")]
    + [("L10", "Cá nhân", None, "26-35", 70, "2026-09-30")]
    + [(f"L1{i}", "SME", None, None, 5000, "2026-09-30") for i in range(1, 6)]
    + [("L15", "SME", None, None, 5000, "2026-09-30")]
    # An older snapshot that must not be counted.
    + [(f"L0{i}", "Cá nhân", None, None, 1, "2026-08-31") for i in range(1, 4)]
)


@pytest.fixture
def credit(warehouse: dict[str, Any], ddl_server: dict[str, str]) -> dict[str, Any]:  # noqa: F811
    conn = psycopg2.connect(ddl_server["dataagent"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bank.customer_loan")
        cur.execute(
            "CREATE TABLE bank.customer_loan (loan_id text, segment text, gender text,"
            " age_band text, balance numeric, as_of_date date)"
        )
        cur.executemany("INSERT INTO bank.customer_loan VALUES (%s,%s,%s,%s,%s,%s)", ROWS)
        cur.execute("GRANT SELECT ON bank.customer_loan TO loans_reader")
    conn.close()
    return warehouse


def catalogue(bank: Bank) -> uuid.UUID:
    seed = bank.seed
    source = seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", bank.loans)
    dataset = seed.row(
        "catalog.dataset",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        origin="source",
        schema_name="bank",
        table_name="customer_loan",
        display_name="Khoản vay khách hàng",
        history_kind="snapshot",
        business_date_col="as_of_date",
        enabled=True,
    )
    for ordinal, (name, label, kind, key) in enumerate(
        [
            ("loan_id", "Mã khoản vay", "string", True),
            ("segment", "Nhóm khách hàng", "string", False),
            ("gender", "Giới tính", "string", False),
            ("age_band", "Độ tuổi", "string", False),
            ("balance", "Dư nợ", "decimal", False),
            ("as_of_date", "Ngày số liệu", "date", True),
        ]
    ):
        seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=dataset,
            dataset_version=1,
            name=name,
            display_name=label,
            ordinal=ordinal,
            data_type=kind,
            normalized_type=kind,
            is_key=key,
        )
    return dataset


AUDIT = (("quality.status", "read"),)


@pytest.mark.req("AC-04-02-01")
async def test_a_data_quality_agent_audits_the_latest_snapshot_of_the_table_it_names(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    catalogue(bank)
    run = agent_run(
        bank, goal="Kiểm toán dữ liệu bảng Khoản vay khách hàng", tools=AUDIT, kind="data_quality"
    )
    await drain()
    status, stop, _, narrative, error, *_ = run_row(seed, run)
    assert (status, stop) == ("succeeded", "completed"), error
    recorded = steps(seed, run)
    assert [(s[1], s[2], s[3]) for s in recorded] == [
        ("plan", "plan", "ok"),
        ("tool_call", "quality.status", "ok"),
    ]
    out = recorded[1][6]
    findings = {f["kind"]: f for f in out["findings"] if f["kind"] != "concentration"}
    texts = [f["text"] for f in out["findings"]]
    # Sixteen rows on 30/09; the three of 31/08 are an older snapshot.
    assert out["snapshot"] == "2026-09-30" and "(16 dòng" in recorded[1][4]
    gender = next(t for t in texts if t.startswith("Giới tính"))
    assert "thiếu thật 1/10 dòng (10,0%) ở Cá nhân" in gender
    assert findings["key"]["text"] == "Khoá Mã khoản vay có 1 giá trị trùng."
    concentration = next(t for t in texts if t.startswith("Giới tính theo Độ tuổi"))
    assert "Nam tập trung ở 26-35 (80,0%, chung 55,6%)" in concentration
    assert "Nữ tập trung ở 36-45 (75,0%, chung 44,4%)" in concentration
    assert "Phân bố Giới tính" in [p["question"] for p in out["parts"]]
    # Every query was read-only, in place, and the run's.
    assert seed.one(
        "SELECT count(*) FILTER (WHERE status = 'succeeded'), count(*) FROM query.query_run"
        " WHERE caller_kind = 'agent_run' AND caller_id = %s",
        run,
    ) == (len(out["query_run_ids"]), len(out["query_run_ids"]))
    assert narrative[-1]["text"] == recorded[1][4]


async def test_a_goal_naming_no_table_of_the_space_lists_them(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    catalogue(bank)
    run = agent_run(bank, goal="Kiểm toán bảng khách hàng VIP", tools=AUDIT, kind="data_quality")
    await drain()
    status, stop, *_, error, _, _, _ = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert "Khoản vay khách hàng" in error and steps(seed, run) == []
