"""The model's proposal for a crawl source's schema (KB-04) as worker-agent sends it back: claimed
from acquire.outbox, answered on acquire.outbox as svc_acquire for the crawler to test."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent import crawl_schemas, questions
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.llm import Reply

from .conftest import Seed

pytestmark = [pytest.mark.integration]


class Script:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.users: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **_: Any
    ) -> Any:
        self.users.append(user)
        return Reply(output.model_validate(self.answer), "responder", 90, 4)


async def test_a_requested_schema_is_proposed_back_to_the_crawler(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức")
    source, request_id, held = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
    seed.row(
        "acquire.outbox",
        org_id=org,
        aggregate_type="crawl_source",
        aggregate_id=source,
        event_type=crawl_schemas.INFERENCE_REQUESTED,
        payload=Json(
            {
                "request_id": request_id,
                "source_id": str(source),
                "data_space_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "content_kind": "html",
                "goal": "Lãi suất",
                "pages": [{"page_id": str(uuid.uuid4()), "kind": "html", "excerpt": "<table>"}],
                "held_out_page_ids": [held],
                "held_out_disjoint": True,
                "fields": [],
            }
        ),
    )
    model = Script(
        {
            "record_selector": "table tr",
            "fields": [{"name": "bank", "type": "text", "locator": "td", "required": True}],
        }
    )
    monkeypatch.setattr(questions, "gateway", lambda: model)

    async with OutboxTableSource(
        schema="acquire", event_types=frozenset(crawl_schemas.HANDLERS)
    ).claim(5) as events:
        for event in events:
            await crawl_schemas.HANDLERS[event.event_type](event)

    assert len(model.users) == 1 and "<table>" in model.users[0]
    payload = seed.one(
        "SELECT payload FROM acquire.outbox WHERE org_id = %s AND event_type = %s",
        org,
        crawl_schemas.DRAFT_PROPOSED,
    )
    assert payload["request_id"] == request_id and payload["held_out_page_ids"] == [held]
    assert payload["draft"]["record_selector"] == "table tr"
    assert seed.one(
        "SELECT published_at IS NOT NULL FROM acquire.outbox WHERE org_id = %s AND event_type = %s",
        org,
        crawl_schemas.INFERENCE_REQUESTED,
    )
