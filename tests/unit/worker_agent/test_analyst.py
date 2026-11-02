"""The Understander's agent pass (understand/analyst.py): the planner works a question out with
tools over the semantic layer, sees only what the asker may, and every reading it proposes is
compiled before it is kept."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest

from services.worker_agent.llm import ModelUnavailable, Reply
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand import analyst
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
)

pytestmark = [pytest.mark.unit]

SALES, SALE = uuid.uuid4(), uuid.uuid4()
DAY = c.Dimension(uuid.uuid4(), "ngay", "Ngày", "order_date", SALE, is_time=True)
COUNTRY = c.Dimension(uuid.uuid4(), "quoc_gia", "Quốc gia", "country", SALE)
COLOR = c.Dimension(uuid.uuid4(), "mau", "Màu", "color", SALE)
REVENUE_ID, QUOTA_ID, SECRET_ID = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def version(code: str, name: str, allowed: set[uuid.UUID]) -> c.MetricVersion:
    return c.MetricVersion(
        id=uuid.uuid4(),
        metric_code=code,
        display_name=name,
        expression="doanh_thu",
        filters=(),
        unit="USD",
        decimals=0,
        period_semantics="sum_over_period",
        allowed_dimension_ids=frozenset(allowed),
        certified=True,
    )


LAYER = SpaceLayer(
    vocabulary=Vocabulary(
        metrics=[
            MetricEntry(REVENUE_ID, "DOANH_THU", "Doanh thu", certified=True),
            MetricEntry(QUOTA_ID, "CHI_TIEU", "Chỉ tiêu doanh số", certified=True),
            MetricEntry(SECRET_ID, "LUONG", "Quỹ lương", certified=True),
        ],
        dimensions=[DimensionEntry(d.id, d.code, d.name, d.is_time) for d in (DAY, COUNTRY, COLOR)],
        values=[ValueEntry("quoc_gia", "United States", "United States")],
        synonyms={
            "revenue": ("metric", str(REVENUE_ID)),
            "my": ("value", "quoc_gia", "United States"),
        },
    ),
    layer=c.Layer(
        measures={"doanh_thu": c.Measure("doanh_thu", "SUM(amount)", SALE)},
        dimensions={d.code: d for d in (DAY, COUNTRY, COLOR)},
        entity_tables={SALE: c.Table(SALES, None, "sales")},
    ),
    versions={
        REVENUE_ID: version("DOANH_THU", "Doanh thu", {COUNTRY.id}),
        QUOTA_ID: version("CHI_TIEU", "Chỉ tiêu doanh số", {COUNTRY.id}),
        SECRET_ID: version("LUONG", "Quỹ lương", set()),
    },
    version_rows={},
    datasets={},
    # The payroll metric is one this asker is denied.
    blocked_metric_ids=frozenset({SECRET_ID}),
)
TODAY = datetime.date(2014, 1, 15)


class Scripted:
    """The planner's turns, in order; what it was shown is kept to check what it saw."""

    def __init__(self, *turns: dict[str, Any]) -> None:
        self.turns = list(turns)
        self.seen: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        self.seen.append(user)
        if not self.turns:
            raise ModelUnavailable("no more turns")
        return Reply(
            output.model_validate(self.turns.pop(0)), model="planner", tokens=50, latency_ms=1
        )


async def resolve(question: str, model: Scripted) -> analyst.Resolved | None:
    return await analyst.resolve(question, LAYER, model, today=TODAY)  # type: ignore[arg-type]


async def test_a_question_the_rules_missed_is_worked_out_with_the_tools() -> None:
    model = Scripted(
        {"tool": "search_metrics", "args": {"text": "revenue"}},
        {"tool": "describe_metric", "args": {"metric": "DOANH_THU"}},
        {"tool": "search_values", "args": {"text": "United States", "dimension": "quoc_gia"}},
        {
            "tool": "propose",
            "args": {
                "metric": "DOANH_THU",
                "group_by": [],
                "filters": {"quoc_gia": ["United States"]},
                "period": "2013",
            },
        },
    )
    found = await resolve("revenue in the US for 2013", model)
    assert found is not None
    u = found.understanding
    assert (u.decision, u.metric.code if u.metric else None) == ("proceed", "DOANH_THU")
    assert u.filters == {"quoc_gia": ["United States"]}
    assert (u.period.start, u.period.end) == (datetime.date(2013, 1, 1), datetime.date(2014, 1, 1))
    assert [s.tool for s in found.steps] == [
        "search_metrics",
        "describe_metric",
        "search_values",
        "propose",
    ]
    # The model read the metric through a synonym, and its cuts from describe_metric.
    assert "DOANH_THU" in model.seen[1] and "quoc_gia" in model.seen[2]


async def test_a_denied_metric_is_never_found_or_described() -> None:
    model = Scripted(
        {"tool": "search_metrics", "args": {"text": "quỹ lương"}},
        {"tool": "describe_metric", "args": {"metric": "LUONG"}},
        {"tool": "propose", "args": {"metric": "LUONG"}},
    )
    assert await resolve("quỹ lương năm 2013", model) is None
    shown = "\n".join(model.seen)
    assert "Quỹ lương" not in shown.split("Câu hỏi:")[-1].split("Kết quả:", 1)[-1]
    assert "không có chỉ số LUONG" in shown


async def test_a_reading_that_does_not_compile_goes_back_to_the_model() -> None:
    # Colour is no cut the owner allows: the compile error is the tool's answer, and the model
    # reads it again without the cut.
    model = Scripted(
        {"tool": "propose", "args": {"metric": "DOANH_THU", "group_by": ["mau"], "period": "2013"}},
        {
            "tool": "propose",
            "args": {"metric": "DOANH_THU", "group_by": ["quoc_gia"], "period": "2013"},
        },
    )
    found = await resolve("revenue theo quốc gia năm 2013", model)
    assert found is not None and found.understanding.group_by == ["quoc_gia"]
    assert "không chạy được" in str(found.steps[0].result)


async def test_an_invented_code_is_refused_and_said() -> None:
    model = Scripted(
        {"tool": "propose", "args": {"metric": "LOI_NHUAN"}},
        {
            "tool": "ask_user",
            "args": {"question": "Ý bạn là?", "options": ["DOANH_THU", "CHI_TIEU"]},
        },
    )
    found = await resolve("lợi nhuận năm 2013", model)
    assert found is not None and found.understanding.decision == "clarify"
    assert [m.code for m in found.understanding.options] == ["DOANH_THU", "CHI_TIEU"]
    assert "không có chỉ số LOI_NHUAN" in str(found.steps[0].result)


async def test_no_model_or_no_answer_in_time_leaves_the_question_to_the_other_paths() -> None:
    assert await resolve("doanh thu", Scripted()) is None
    looping = Scripted(
        *[{"tool": "search_metrics", "args": {"text": "x"}}] * (analyst.MAX_TURNS + 2)
    )
    assert await resolve("doanh thu", looping) is None
    assert len(looping.seen) == analyst.MAX_TURNS
