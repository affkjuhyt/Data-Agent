"""The `__evidence` frame — "why should I trust this number" on the wire.

Before this frame the facts existed only in state: which SQL ran, whether an
approved metric contract compiled it, how many rows, how fresh the tables were,
whether the join shape could multiply a total. None of it reached a client, so
the UI could show a number but never its provenance.

Three things are pinned here:

* the responder builds the payload deterministically from tool metadata — it
  never asks the model what it did;
* the workflow_runtime stream emits it, and only for a delivered answer;
* the orchestrator forwards it (and `__options`, which it used to drop — the
  reason clarification choices never reached the Playground).
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RUNTIME_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
_ORCH_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime" / "orchestrator")

pytestmark = pytest.mark.unit


@pytest.fixture
def runtime_path():
    added = [p for p in (_RUNTIME_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _responder():
    import nodes.model_harness.responder as responder
    return responder


def _llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return resp


_ROWS = [{"region": "Bắc", "revenue": 1200}, {"region": "Nam", "revenue": 800}]

_SQL_CHUNK = {
    "id": "sql_abc",
    "content": json.dumps(_ROWS),
    "source": "tool/sql",
    "rrf_sources": ["db"],
    "metadata": {
        "sql": "SELECT region, SUM(amount) AS revenue FROM fact_sales GROUP BY region",
        "row_count": 2,
        "db_name": "dw",
        "db_type": "postgres",
        "generated_by": "semantic_layer",
        "semantic": {"metric": "doanh_thu_thuan@3", "owner": "Finance"},
        "provenance": {"status": "fresh", "as_of": "2026-08-29T23:00:00+00:00"},
        "aggregation": {"findings": [], "structurally_safe": True, "joins": 0},
        "truncation": None,
    },
}

_KB_CHUNK = {
    "id": "kb_1",
    "content": "Quy định ghi nhận doanh thu ...",
    "source": "policy.pdf",
    "rrf_sources": ["kb"],
    "metadata": {},
}


class TestEvidenceRunIsBuiltFromMetadata:
    def test_sql_chunk_becomes_a_run_with_columns_and_rows(self, runtime_path):
        run = _responder()._evidence_run(_SQL_CHUNK)

        assert run is not None
        assert run["kind"] == "sql"
        assert run["sql"].startswith("SELECT region")
        assert run["columns"] == ["region", "revenue"]
        assert run["rows"] == _ROWS
        assert run["row_count"] == 2
        assert run["generated_by"] == "semantic_layer"
        assert run["metric"]["metric"] == "doanh_thu_thuan@3"
        assert run["provenance"]["status"] == "fresh"
        assert run["rows_capped"] is False

    def test_model_written_sql_is_labelled_llm_not_left_blank(self, runtime_path):
        chunk = {**_SQL_CHUNK, "metadata": {k: v for k, v in _SQL_CHUNK["metadata"].items() if k != "generated_by"}}
        run = _responder()._evidence_run(chunk)
        assert run is not None
        assert run["generated_by"] == "llm"

    def test_kb_chunk_is_not_evidence_of_a_query(self, runtime_path):
        assert _responder()._evidence_run(_KB_CHUNK) is None

    def test_rows_are_capped_and_the_cap_is_declared(self, runtime_path):
        responder = _responder()
        many = [{"i": n} for n in range(responder._EVIDENCE_MAX_ROWS + 5)]
        run = responder._evidence_run({**_SQL_CHUNK, "content": json.dumps(many)})
        assert run is not None
        assert len(run["rows"]) == responder._EVIDENCE_MAX_ROWS
        assert run["rows_capped"] is True

    def test_unparseable_content_still_yields_the_sql(self, runtime_path):
        run = _responder()._evidence_run({**_SQL_CHUNK, "content": "not json"})
        assert run is not None
        assert run["rows"] == []
        assert run["columns"] == []


class TestResponderReturnsEvidence:
    async def test_runs_and_clarifications_ride_on_the_node_output(self, runtime_path, monkeypatch):
        responder = _responder()
        monkeypatch.setattr(responder.litellm, "acompletion", AsyncMock(return_value=_llm("Doanh thu 2.000 [1]")))

        clar = [{"reason": "missing_time_range", "text": "Kỳ nào?", "options": ["this_month"]}]
        state = {
            "query": "doanh thu theo vùng",
            "config": {"system_prompt": "Bạn là trợ lý dữ liệu."},
            "messages": [],
            "reranked_chunks": [_SQL_CHUNK, _KB_CHUNK],
            "clarifications": clar,
        }
        out = await responder.responder_node(state)

        assert "evidence" in out
        ev = out["evidence"]
        assert [r["id"] for r in ev["runs"]] == ["sql_abc"]
        assert ev["runs"][0]["citation_index"] == 1
        assert ev["clarifications"] == clar
        assert ev["warnings"] == []
        # citations are still only what the model actually cited
        assert [c["index"] for c in out["citations"]] == [1]

    async def test_no_chunks_still_returns_an_empty_evidence_shape(self, runtime_path, monkeypatch):
        responder = _responder()
        monkeypatch.setattr(responder.litellm, "acompletion", AsyncMock(return_value=_llm("Xin chào")))
        out = await responder.responder_node({
            "query": "hi", "config": {"system_prompt": "x"}, "messages": [],
        })
        assert out["evidence"] == {"runs": [], "clarifications": [], "warnings": []}


class TestRuntimeStreamEmitsEvidence:
    def test_conversation_runner_emits_evidence_only_on_a_delivered_answer(self):
        src = (REPO_ROOT / "backend" / "services" / "workflow_runtime" / "runtime_services" / "conversation_runner.py").read_text(encoding="utf-8")
        assert "'__evidence'" in src
        # captured from the responder node output ...
        assert 'out.get("evidence")' in src
        # ... and assigned inside the non-refused branch, next to the citations
        refused_branch = src.index("if triggered:")
        delivered = src.index("pending_evidence = last_responder_evidence")
        assert delivered > refused_branch


class TestOrchestratorForwardsEvidenceAndOptions:
    """The orchestrator sits between workflow_runtime and the browser. A frame
    it does not forward does not exist as far as the UI is concerned."""

    @pytest.fixture
    def agent_client(self, runtime_path):
        """orchestrator/executor/agent_client.py loaded by path — with
        workflow_runtime on sys.path (`runtime_path`) for its
        `orchestrator.config` import, but without executing the
        `orchestrator.executor` package __init__, which pulls in the planner,
        litellm and langfuse for nothing this test needs."""
        spec = importlib.util.spec_from_file_location(
            "orch_agent_client_under_test", pathlib.Path(_ORCH_DIR) / "executor" / "agent_client.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    async def test_agent_client_yields_options_and_evidence(self, agent_client, monkeypatch):
        ac = agent_client

        frames = [
            'data: "Doanh thu"',
            'data: {"__evidence": {"runs": [{"sql": "SELECT 1"}]}}',
            'data: {"__options": ["Tháng này", "Tháng trước"]}',
            'data: {"__suggestions": ["x"]}',
            "data: [DONE]",
        ]

        # What run_conversation_stream returns: a StreamingResponse whose body
        # iterator yields SSE chunks (here one frame per chunk, as the runtime
        # emits them).
        async def _body():
            for f in frames:
                yield f + "\n\n"

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"X-Conversation-Id": "conv-1"}
        resp.body_iterator = _body()

        monkeypatch.setattr(ac, "_run_agent", AsyncMock(return_value=resp))

        events = [e async for e in ac.call_agent_stream("agent-1", "doanh thu", tenant_id="t1")]

        assert ("conv_id", "conv-1") in events
        assert ("token", "Doanh thu") in events
        assert ("evidence", {"runs": [{"sql": "SELECT 1"}]}) in events
        assert ("options", ["Tháng này", "Tháng trước"]) in events
        # suggestions are the orchestrator's own job — still not forwarded
        assert not any(e[0] == "suggestions" for e in events)

    def test_single_agent_node_and_sse_route_pass_them_through(self):
        graph_src = (pathlib.Path(_ORCH_DIR) / "graph.py").read_text(encoding="utf-8")
        stream_src = (pathlib.Path(_ORCH_DIR) / "routers" / "stream.py").read_text(encoding="utf-8")
        for name in ("options", "evidence"):
            assert f'adispatch_custom_event("{name}", data)' in graph_src
            assert f'"__{name}": event["data"]' in stream_src

    def test_docstring_declares_the_new_events(self, agent_client):
        ac = agent_client
        doc = inspect.getdoc(ac.call_agent_stream) or ""
        assert '("options"' in doc and '("evidence"' in doc
