"""A question the semantic layer cannot answer is answered by SQL the model writes (01 §3.4,
worker-agent sqlgen/): six candidates checked, repaired once, run read-only in place, voted on;
the answer labelled "Khám phá", or a narrower question when the candidates disagree."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import questions
from services.worker_agent.llm import ModelUnavailable, Reply

from .conftest import Seed
from .test_answers import (
    Bank,
    answer_of,
    build,
    drain,
    warehouse,  # noqa: F401  (the fixture `credit` reads)
)

pytestmark = [pytest.mark.integration]

QUESTION = "Số khoản vay theo mã chi nhánh ngày 30/09/2026"
GOOD = [
    "SELECT branch_code AS ma_chi_nhanh, COUNT(*) AS so_khoan_vay FROM bank.loan "
    "WHERE as_of_date = DATE '2026-09-30' GROUP BY branch_code",
    "SELECT l.branch_code, COUNT(l.branch_code) AS n FROM bank.loan AS l "
    "WHERE l.as_of_date = '2026-09-30' GROUP BY l.branch_code ORDER BY 1",
]


@pytest.fixture
def credit(warehouse: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    return warehouse


class Script:
    """The model as a script: text_to_sql answers in the order asked; the planner is away."""

    def __init__(self, sqls: list[str]) -> None:
        self.sqls = list(sqls)
        self.asked: list[tuple[str, float]] = []

    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply:
        if role != "text_to_sql":
            raise ModelUnavailable("only the SQL model is up in this test")
        self.asked.append((user, temperature))
        return Reply({"sql": self.sqls.pop(0)}, "agent-main", 100, 5)


def catalogue(bank: Bank) -> None:
    """The columns of the credit warehouse, as a schema scan writes them."""
    seed = bank.seed
    branches = seed.one(
        "SELECT id FROM catalog.dataset WHERE org_id = %s AND table_name = 'branch'", bank.org
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.dataset SET display_name = 'Khoản vay' WHERE id = %s",
            (str(bank.loans),),
        )
    for dataset, cols in (
        (
            bank.loans,
            [
                ("branch_code", "Mã chi nhánh", "string"),
                ("group_code", "Nhóm nợ", "integer"),
                ("balance", "Dư nợ", "decimal"),
                ("product_code", "Sản phẩm", "string"),
                ("as_of_date", "Ngày số liệu", "date"),
            ],
        ),
        (branches, [("code", "Mã chi nhánh", "string"), ("region_name", "Vùng", "string")]),
    ):
        for ordinal, (name, label, kind) in enumerate(cols):
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
            )


def asked(bank: Bank, script: Script, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setattr(questions, "_gateway", script)
    monkeypatch.setattr(questions, "_gateway_read", True)
    catalogue(bank)
    return bank.ask(QUESTION)


async def test_six_candidates_that_agree_answer_with_the_exploratory_label(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    script = Script([GOOD[0], GOOD[1]] * 3)
    q = asked(bank, script, monkeypatch)
    await drain()
    status, kind, text, figures, scope, answer = answer_of(seed, q)
    assert (status, kind, scope) == ("answered", "data", "not_applicable"), text
    assert "6/6 cách viết cho cùng kết quả" in text and "Khám phá" in text
    # Three ways twice, the second sample of each warmer than the first.
    assert sorted(t for _, t in script.asked) == [0.2, 0.2, 0.2, 0.7, 0.7, 0.7]
    # Only the linked tables were shown to the model, with their columns and labels.
    assert "TABLE bank.loan -- Khoản vay" in script.asked[0][0]
    assert all(f["trust"] == "Khám phá (truy vấn do mô hình viết)" for f in figures)
    assert {f["dimensions"]["ma_chi_nhanh"] for f in figures} >= {"HN1", "SG1"}
    plan = seed.one("SELECT steps FROM qa.plan WHERE question_id = %s", q)
    assert plan["path"] == "exploratory_sql" and len(plan["candidates"]) == 6
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_kind = 'question' AND caller_id = %s",
            q,
        )
        == 6
    )
    assert seed.one("SELECT chart_spec->>'type' FROM qa.answer WHERE id = %s", answer) == "bar"


async def test_a_candidate_the_checker_rejects_is_repaired_once_and_still_counts(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    # The first candidate reads every column; sent back with the instruction, it names them.
    script = Script(["SELECT * FROM bank.loan"] + [GOOD[0]] * 5 + [GOOD[1]])
    q = asked(bank, script, monkeypatch)
    await drain()
    status, _, text, *_ = answer_of(seed, q)
    assert status == "answered" and "6/6" in text
    repair = script.asked[-1][0]
    assert "SELECT *" in repair and "Không dùng SELECT *" in repair


async def test_candidates_that_disagree_give_no_number_and_ask_back(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    differing = [
        GOOD[0],
        GOOD[1],
        "SELECT branch_code, SUM(balance) FROM bank.loan GROUP BY branch_code",
        "SELECT product_code, COUNT(*) FROM bank.loan GROUP BY product_code",
        "SELECT COUNT(*) FROM bank.loan",
        "SELECT MAX(balance) FROM bank.loan",
    ]
    q = asked(bank, Script(differing), monkeypatch)
    await drain()
    status, kind, text, figures, *_ = answer_of(seed, q)
    assert (status, kind, figures) == ("clarifying", "clarification", [])
    assert "2/6" in text and json.dumps(figures) == "[]"
