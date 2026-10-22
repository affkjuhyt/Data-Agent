"""The LiteLLM gateway over a scripted OpenAI-compatible server: busy answers are retried, an answer
that does not fit its model is asked again once, and anything else falls back to ModelUnavailable."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from pydantic import BaseModel

from services.worker_agent.llm import LiteLLMGateway, ModelUnavailable

pytestmark = [pytest.mark.unit]


class Draft(BaseModel):
    sql: str


class Server:
    def __init__(self) -> None:
        self.script: list[tuple[int, str]] = []
        self.bodies: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                server.bodies.append(
                    json.loads(self.rfile.read(int(self.headers["content-length"])))
                )
                status, content = server.script.pop(0)
                body = {
                    "id": "c",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "agent-main",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
                data = json.dumps(body if status == 200 else {"error": {"message": "busy"}})
                self.send_response(status)
                self.send_header("content-type", "application/json")
                # The SDK honours this, so a retried 429 does not wait its default backoff.
                self.send_header("retry-after-ms", "1")
                self.end_headers()
                self.wfile.write(data.encode())

            def log_message(self, *args: Any) -> None:
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_port}"


@pytest.fixture
def server() -> Iterator[Server]:
    s = Server()
    thread = threading.Thread(target=s.http.serve_forever, daemon=True)
    thread.start()
    yield s
    s.http.shutdown()


async def test_a_busy_gateway_is_retried_and_the_answer_is_typed(server: Server) -> None:
    server.script = [(429, ""), (200, '{"sql": "SELECT 1"}')]
    reply = await LiteLLMGateway(server.url, "k").json_chat(
        "text_to_sql", "rules", "question", Draft, max_tokens=50
    )
    assert reply.content == Draft(sql="SELECT 1")
    assert (reply.model, reply.tokens) == ("agent-main", 15)
    sent = server.bodies[-1]
    assert sent["model"] == "text_to_sql"
    assert sent["messages"][0] == {"role": "system", "content": "rules"}
    assert sent["response_format"]["json_schema"]["schema"]["required"] == ["sql"]


async def test_an_answer_that_does_not_fit_is_asked_again_with_its_errors(server: Server) -> None:
    server.script = [(200, '{"query": "SELECT 1"}'), (200, '{"sql": "SELECT 2"}')]
    reply = await LiteLLMGateway(server.url, "k").json_chat("text_to_sql", "rules", "q", Draft)
    assert reply.content.sql == "SELECT 2"
    assert reply.tokens == 30
    assert "sql" in server.bodies[-1]["messages"][-1]["content"]


async def test_no_usable_answer_is_model_unavailable(server: Server) -> None:
    server.script = [(200, "not json"), (200, "{}")]
    with pytest.raises(ModelUnavailable):
        await LiteLLMGateway(server.url, "k").json_chat("planner", "s", "q", Draft)
    server.script = [(500, "")] * 3
    with pytest.raises(ModelUnavailable):
        await LiteLLMGateway(server.url, "k").json_chat("planner", "s", "q", Draft)
