"""common/llm.ask over a scripted OpenAI-compatible server: the answer is typed,
an answer that never fits is None, and a gateway error reaches the caller's own
recovery path (event retry + DLQ, next crawl tick)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError

from services.platform_workers.common import llm
from services.platform_workers.common.settings import LiteLLMSettings

pytestmark = [pytest.mark.unit]


class Answer(BaseModel):
    keywords: list[str]


class Server:
    def __init__(self) -> None:
        self.script: list[tuple[int, str]] = []
        self.bodies: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["content-length"])
                server.bodies.append(json.loads(self.rfile.read(length)))
                status, content = server.script.pop(0)
                body = {
                    "id": "c",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "agent-fast",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                data = json.dumps(body if status == 200 else {"error": {"message": "down"}})
                self.send_response(status)
                self.send_header("content-type", "application/json")
                # The SDK honours this, so its retries do not wait their default backoff.
                self.send_header("retry-after-ms", "1")
                self.end_headers()
                self.wfile.write(data.encode())

            def log_message(self, *args: Any) -> None:
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.settings = LiteLLMSettings(
            base_url=f"http://127.0.0.1:{self.http.server_port}", api_key="k"
        )


@pytest.fixture
def server() -> Iterator[Server]:
    s = Server()
    threading.Thread(target=s.http.serve_forever, daemon=True).start()
    yield s
    s.http.shutdown()


def test_the_answer_is_typed_and_the_schema_is_sent(server: Server) -> None:
    server.script = [(200, '{"keywords": ["OHLCV"]}')]
    assert llm.ask("p", Answer, "planner", settings=server.settings) == Answer(keywords=["OHLCV"])
    sent = server.bodies[0]
    assert sent["model"] == "planner"
    assert sent["messages"] == [{"role": "user", "content": "p"}]
    assert sent["response_format"]["json_schema"]["schema"]["required"] == ["keywords"]


def test_an_answer_that_never_fits_is_none(server: Server) -> None:
    server.script = [(200, "not json"), (200, '{"keywords": "OHLCV"}')]
    assert llm.ask("p", Answer, "planner", settings=server.settings) is None
    assert len(server.bodies) == 2


def test_a_gateway_error_reaches_the_caller(server: Server) -> None:
    server.script = [(503, "")] * 3
    with pytest.raises(ModelHTTPError):
        llm.ask("p", Answer, "planner", settings=server.settings)
