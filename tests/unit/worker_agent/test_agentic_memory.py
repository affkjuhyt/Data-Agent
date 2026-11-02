"""The dynamic agent's long-term memory (worker_agent/agentic/memory.py): what is kept, what is
recalled for a question, and how it reaches the planner."""

from __future__ import annotations

import datetime

import pytest

from services.worker_agent.agentic import memory

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 11, 3, tzinfo=datetime.UTC)


def _m(kind: str, content: str, days_ago: int = 1, times: int = 1) -> memory.Memory:
    return memory.Memory(kind, content, times, NOW - datetime.timedelta(days=days_ago))


def test_a_proposal_quoting_a_fetched_figure_or_a_secret_is_never_kept() -> None:
    fetched = {"17.429", "17429"}
    keep = memory.MemoryOp(kind="preference", content="Luôn hiển thị số theo triệu đồng")
    figure = memory.MemoryOp(kind="fact", content="Năm 2013 có 17.429 khách hàng mua online")
    secret = memory.MemoryOp(kind="fact", content="Mật khẩu kho là abc123")
    url = memory.MemoryOp(kind="fact", content="Kho ở postgres://user:pw@host/db")
    assert memory._kept(keep, fetched)
    assert not memory._kept(figure, fetched)
    assert not memory._kept(secret, fetched)
    assert not memory._kept(url, fetched)


def test_recall_ranks_by_words_in_common_and_keeps_preferences_for_every_question() -> None:
    known = [
        _m("definition", "Khách hàng mới là khách mua lần đầu trong năm", days_ago=10),
        _m("fact", "Tôi phụ trách vùng Europe", days_ago=2),
        _m("preference", "Trả lời ngắn, có biểu đồ", days_ago=5, times=3),
        _m("fact", "Quan tâm nhóm hàng Bikes", days_ago=200),
    ]
    found = memory.rank("Có bao nhiêu khách hàng mới năm 2013", known, NOW)
    assert found[0].content.startswith("Khách hàng mới")
    assert any(m.kind == "preference" for m in found)
    assert all("Europe" not in m.content for m in found)


def test_same_idea_said_again_hashes_the_same() -> None:
    assert memory.content_hash("Trả lời  ngắn gọn") == memory.content_hash("trả lời ngắn gọn")


def test_recalled_memories_reach_the_planner_with_when_they_were_said() -> None:
    text = memory.render([_m("preference", "Dùng đơn vị triệu", days_ago=3, times=2)])
    assert text.startswith("<memory>") and "nói lúc 2026-10-31, 2 lần" in text
    assert memory.render([]) == ""
