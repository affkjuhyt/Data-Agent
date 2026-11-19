"""The governed-first router (router.py, AE-01): which engine a question goes to, by rules."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent import router
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    Vocabulary,
    understand,
)

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2013, 12, 31)
REVENUE = MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", certified=True)
PROFIT = MetricEntry(uuid.uuid4(), "LOI_NHUAN", "Lợi nhuận", certified=True)
MARGIN_WEB = MetricEntry(uuid.uuid4(), "LAI_GOP_WEB", "Lãi gộp Internet", certified=True)
MARGIN_SHOP = MetricEntry(uuid.uuid4(), "LAI_GOP_DL", "Lãi gộp đại lý", certified=True)
REGION = DimensionEntry(uuid.uuid4(), "vung", "vùng")
VOCAB = Vocabulary(
    metrics=[REVENUE, PROFIT, MARGIN_WEB, MARGIN_SHOP],
    dimensions=[REGION],
    synonyms={
        "revenue": ("metric", str(REVENUE.metric_id)),
        "ngan sach": ("metric", str(PROFIT.metric_id)),
    },
)


def route(text: str, *, deep: bool = False) -> router.Route:
    return router.classify(text, understand(text, VOCAB, today=TODAY), deep=deep)


@pytest.mark.parametrize(
    ("text", "path", "reason"),
    [
        # One certified metric, however it is cut, compared or filtered: the governed path.
        ("Doanh thu năm 2013", "governed", "RESOLVED"),
        ("Doanh thu theo vùng năm 2013", "governed", "RESOLVED"),
        ("Doanh thu tháng 9/2013 so với tháng trước", "governed", "RESOLVED"),
        ("Revenue in 2013", "governed", "RESOLVED"),
        # A name said in part: the governed path asks which, under the clarify policy.
        ("Lãi gộp năm 2013", "governed", "AMBIGUOUS"),
        # Nothing placed: the governed path's model reader, analyst, SQL and documents.
        ("Giá cổ phiếu của công ty hôm nay", "governed", "UNCATALOGUED"),
        # What only the agent does.
        ("Vì sao doanh thu năm 2013 giảm?", "agentic", "WHY"),
        ("Why did revenue fall in 2013?", "agentic", "WHY"),
        ("Doanh thu và lợi nhuận năm 2013", "agentic", "MULTI_METRIC"),
        # Named unequally (a name, a synonym): one qualifies the other, the governed path reads it
        # ("Doanh số Trade Sales theo ngân sách", DS-AW aw057/aw058).
        ("Doanh thu theo ngân sách năm 2013", "governed", "RESOLVED"),
        ("So sánh doanh thu với lợi nhuận năm 2013", "agentic", "MULTI_METRIC"),
        ("Doanh thu năm 2013 theo quy định ghi nhận mới", "agentic", "HYBRID"),
        ("Chính sách đổi trả hàng là gì?", "agentic", "DOC_ONLY"),
        ("What does the return policy say?", "agentic", "DOC_ONLY"),
    ],
)
def test_a_question_goes_where_it_can_be_answered(text: str, path: str, reason: str) -> None:
    assert route(text) == router.Route(path, reason)  # type: ignore[arg-type]


def test_a_turn_asked_to_go_deep_goes_to_the_agent_whatever_it_names() -> None:
    assert route("Doanh thu năm 2013", deep=True) == router.Route("agentic", "DEEP")


def test_two_metrics_named_apart_count_as_two() -> None:
    u = understand("Doanh thu và lợi nhuận năm 2013", VOCAB, today=TODAY)
    assert u.named_metrics == 2
    assert understand("Doanh thu năm 2013", VOCAB, today=TODAY).named_metrics == 1


@pytest.mark.parametrize(
    ("value", "policy"),
    [
        (None, "router"),
        ("router", "router"),
        ("AGENTIC", "agentic"),
        ("governed", "governed"),
        ("x", "router"),
    ],
)
def test_ask_engine_picks_the_policy(
    monkeypatch: pytest.MonkeyPatch, value: str | None, policy: str
) -> None:
    if value is None:
        monkeypatch.delenv("ASK_ENGINE", raising=False)
    else:
        monkeypatch.setenv("ASK_ENGINE", value)
    assert router.engine() == policy


async def test_a_fixed_policy_routes_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.worker_agent.events import Event

    event = Event(uuid.uuid4(), uuid.uuid4(), "question.received", "question", uuid.uuid4(), {})
    monkeypatch.setenv("ASK_ENGINE", "governed")
    assert await router.decide(event, uuid.uuid4()) == router.Route("governed", "ENGINE_GOVERNED")
    monkeypatch.setenv("ASK_ENGINE", "agentic")
    assert await router.decide(event, uuid.uuid4()) == router.Route("agentic", "ENGINE_AGENTIC")
