"""The agent pass (understand/analyst.py) on the bank of test_answers.py: a question neither the
rules nor the one-shot reading place is worked out with the tools, compiled, run and answered with
the figure the SQL computed, never one the model wrote."""

from __future__ import annotations

from typing import Any

import pytest

from libs.dataagent_db import scoped_session
from services.worker_agent import questions
from services.worker_agent.llm import ModelUnavailable, Reply
from services.worker_agent.semantic import loader
from services.worker_agent.understand import analyst
from services.worker_agent.understand.analyst import AgentTurn

from . import test_answers
from .conftest import Seed
from .test_answers import answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


class Planner:
    """Only the agent's turns are answered; the one-shot reading and the narrator are down."""

    def __init__(self, *turns: dict[str, Any]) -> None:
        self.turns = list(turns)
        self.calls: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        self.calls.append(output.__name__)
        if output is not AgentTurn or not self.turns:
            raise ModelUnavailable("only the agent's turns are scripted")
        return Reply(
            output.model_validate(self.turns.pop(0)), model="planner", tokens=40, latency_ms=1
        )


async def test_the_agent_works_out_a_question_and_the_figure_is_the_sqls(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    planner = Planner(
        {"tool": "search_metrics", "args": {"text": "dư nợ"}},
        {"tool": "describe_metric", "args": {"metric": "DU_NO"}},
        {"tool": "propose", "args": {"metric": "DU_NO", "period": "2026-09"}},
    )
    monkeypatch.setattr(questions, "gateway", lambda: planner)
    q = bank.ask("Tổng tiền đang cho vay ra tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    # 30/09 without internal lending: 800 + 200 + 1000 + 900 + 100, from the warehouse.
    assert [float(f["value"]) for f in figures] == [3000.0]
    assert planner.calls[0] == "Reading" and planner.calls[1:4] == ["AgentTurn"] * 3


async def test_values_of_a_withheld_or_masked_column_are_never_offered(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # search_values lists members to the planner and the timeline, so the loader keeps only the
    # members a reader may see: not those of a masked column, nor of a withheld table.
    bank = build(seed, warehouse, monkeypatch)
    product = seed.one(
        "SELECT id FROM context.semantic_dimension WHERE data_space_id = %s AND code = 'san_pham'",
        bank.space,
    )
    seed.row(
        "context.value_index",
        org_id=bank.org,
        data_space_id=bank.space,
        dimension_id=product,
        value_code="VAY_NHA",
        value_label="Vay mua nhà",
        label_norm="vay mua nha",
    )
    seed.row(
        "context.synonym",
        org_id=bank.org,
        data_space_id=bank.space,
        phrase="mua nhà",
        phrase_norm="mua nha",
        target_kind="value",
        target_id=product,
        target_value="VAY_NHA",
        origin="manual",
        status="active",
    )

    async def offered() -> list[str]:
        async with scoped_session("svc_qa", bank.org, bank.user) as session:
            layer = await loader.load(session, bank.space)
        found = analyst.search_values(layer, "vay mua nhà", None) + analyst.search_values(
            layer, "mua nhà", None
        )
        found += analyst.search_values(layer, "Miền Bắc", None)
        return sorted({str(v["dimension"]) for v in found})

    assert await offered() == ["san_pham", "vung"]
    policy = {
        "org_id": bank.org,
        "data_space_id": bank.space,
        "status": "active",
        "simulated_at": "2026-10-17T08:00:00Z",
        "activated_at": "2026-10-17T09:00:00Z",
        "activated_by": bank.user,
    }
    seed.row(
        "governance.access_policy",
        dataset_id=bank.loans,
        name="Che mã sản phẩm",
        kind="mask",
        columns=["product_code"],
        mask_method="last4",
        **policy,
    )
    assert await offered() == ["vung"]
    branches = seed.one(
        "SELECT id FROM catalog.dataset WHERE data_space_id = %s AND table_name = 'branch'",
        bank.space,
    )
    seed.row(
        "governance.access_policy",
        dataset_id=branches,
        name="Ẩn chi nhánh",
        kind="column",
        columns=["region_name"],
        **policy,
    )
    assert await offered() == []


async def test_a_proposal_the_words_only_partly_name_is_asked_back_not_answered(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seen on DS-AW: "Doanh thu Internet năm 2013" with only another sales metric certified. The
    # planner proposes the nearest one; the question's words name it in part, so the asker
    # confirms it rather than getting another quantity's number.
    bank = build(seed, warehouse, monkeypatch)
    planner = Planner(
        {"tool": "propose", "args": {"metric": "DU_NO", "period": "2026-09"}},
    )
    monkeypatch.setattr(questions, "gateway", lambda: planner)
    q = bank.ask("Khoản nợ thẻ tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind, figures) == ("clarifying", "clarification", []), text
