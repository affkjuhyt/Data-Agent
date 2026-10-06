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

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_RUNTIME_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
_ORCH_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime" / "application" / "orchestrator")

pytestmark = pytest.mark.unit


@pytest.fixture
def runtime_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
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

_SQL_ITEM = {
    "id": "sql_abc",
    "content": json.dumps(_ROWS),
    "source": "tool/sql",
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


def _result_set(item=_SQL_ITEM):
    from services.workflow_runtime.application import result_sets
    rs, _ = result_sets.from_handler_item(item)
    return rs


class TestEvidenceRunIsBuiltFromTheResultSet:
    """`__evidence.runs[]` keeps its pre-R0-1 shape for one release, rebuilt
    from the ResultSet so it cannot disagree with the `__result_set` frame."""

    def test_sql_result_becomes_a_run_with_columns_and_rows(self, runtime_path):
        from services.workflow_runtime.application import result_sets
        rs = _result_set()
        run = result_sets.evidence_run(rs, citation_index=1)

        assert run["id"] == rs["result_id"]
        assert run["kind"] == "sql"
        assert run["sql"].startswith("SELECT region")
        assert run["columns"] == ["region", "revenue"]
        assert run["rows"] == _ROWS
        assert run["row_count"] == 2
        assert run["generated_by"] == "semantic_layer"
        assert run["metric"]["metric"] == "doanh_thu_thuan@3"
        assert run["provenance"]["status"] == "fresh"
        assert run["rows_capped"] is False
        assert run["citation_index"] == 1
        # Nothing stored, so no download is offered.
        assert "export" not in run

    def test_model_written_sql_is_labelled_llm_and_exploratory(self, runtime_path):
        md = {k: v for k, v in _SQL_ITEM["metadata"].items() if k != "generated_by"}
        rs = _result_set({**_SQL_ITEM, "metadata": md})
        assert rs["provenance"]["generated_by"] == "llm"
        assert rs["trust"] == "EXPLORATORY"

    def test_unparseable_content_still_yields_the_sql(self, runtime_path):
        from services.workflow_runtime.application import result_sets
        run = result_sets.evidence_run(_result_set({**_SQL_ITEM, "content": "not json"}))
        assert run["rows"] == []
        assert run["columns"] == []
        assert run["sql"].startswith("SELECT region")


class TestResponderReturnsEvidence:
    async def test_runs_and_clarifications_ride_on_the_node_output(self, runtime_path, monkeypatch):
        responder = _responder()
        monkeypatch.setattr(responder.litellm, "acompletion", AsyncMock(return_value=_llm("Doanh thu 2.000 [1]")))

        clar = [{"reason": "missing_time_range", "text": "Kỳ nào?", "options": ["this_month"]}]
        rs = _result_set()
        state = {
            "query": "doanh thu theo vùng",
            "config": {"system_prompt": "Bạn là trợ lý dữ liệu."},
            "messages": [],
            "result_sets": [rs],
            "reranked_chunks": [_KB_CHUNK],
            "clarifications": clar,
        }
        out = await responder.responder_node(state)

        ev = out["evidence"]
        assert [r["id"] for r in ev["runs"]] == [rs["result_id"]]
        assert ev["runs"][0]["citation_index"] == 1
        assert ev["clarifications"] == clar
        assert ev["warnings"] == []
        # citations are still only what the model actually cited
        assert [c["index"] for c in out["citations"]] == [1]
        assert out["citations"][0]["type"] == "db"
        # The tables the answer was built from, for the `__result_set` frames.
        assert [f["result_id"] for f in out["_result_sets"]] == [rs["result_id"]]
        assert out["_result_sets"][0]["citation_index"] == 1

    async def test_no_context_still_returns_an_empty_evidence_shape(self, runtime_path, monkeypatch):
        responder = _responder()
        monkeypatch.setattr(responder.litellm, "acompletion", AsyncMock(return_value=_llm("Xin chào")))
        out = await responder.responder_node({
            "query": "hi", "config": {"system_prompt": "x"}, "messages": [],
        })
        assert out["evidence"] == {"runs": [], "clarifications": [], "warnings": []}
        assert out["_result_sets"] == []


class TestRuntimeStreamEmitsEvidence:
    def test_conversation_runner_emits_evidence_only_on_a_delivered_answer(self):
        src = (REPO_ROOT / "backend" / "services" / "workflow_runtime" / "application" / "runtime_services" / "conversation_runner.py").read_text(encoding="utf-8")
        assert "'__evidence'" in src
        # captured from the responder node output ...
        assert 'out.get("evidence")' in src
        # ... and assigned inside the non-refused branch, next to the citations
        refused_branch = src.index("if triggered:")
        delivered = src.index("pending_evidence = last_responder_evidence")
        assert delivered > refused_branch
        # Result sets follow the same rule, and go out before `__evidence`.
        assert src.index("pending_result_sets = last_responder_result_sets") > refused_branch
        assert src.index("'__result_set'") < src.index("'__evidence'")


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
            'data: {"__result_set": {"result_id": "abc"}}',
            'data: {"__refusal": {"code": "PLANNER_ERROR"}}',
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
        assert ("result_set", {"result_id": "abc"}) in events
        assert ("refusal", {"code": "PLANNER_ERROR"}) in events
        assert ("evidence", {"runs": [{"sql": "SELECT 1"}]}) in events
        assert ("options", ["Tháng này", "Tháng trước"]) in events
        # suggestions are the orchestrator's own job — still not forwarded
        assert not any(e[0] == "suggestions" for e in events)

    async def test_agent_client_continues_the_thread_and_relays_answers(self, agent_client, monkeypatch):
        """Without the conversation id every turn is a fresh thread: no
        inherited slots, no "just asked" marker — and the answer is asked again."""
        ac = agent_client

        async def _body():
            yield "data: [DONE]\n\n"

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"X-Conversation-Id": "conv-1"}
        resp.body_iterator = _body()
        run = AsyncMock(return_value=resp)
        monkeypatch.setattr(ac, "_run_agent", run)

        answers = [{"reason": "missing_time_range", "value": "this_month"}]
        _ = [e async for e in ac.call_agent_stream(
            "agent-1", "doanh thu", tenant_id="t1",
            conversation_id="conv-1", clarification_answers=answers,
        )]

        payload = run.await_args.args[0]
        assert payload["conversation_id"] == "conv-1"
        assert payload["clarification_answers"] == answers

    def test_single_agent_node_and_sse_route_pass_them_through(self):
        graph_src = (pathlib.Path(_ORCH_DIR) / "graph.py").read_text(encoding="utf-8")
        stream_src = (pathlib.Path(_ORCH_DIR).parents[1] / "api" / "routers" / "orchestrator" / "stream.py").read_text(encoding="utf-8")
        for name in ("options", "evidence", "result_set", "refusal"):
            assert f'adispatch_custom_event("{name}", data)' in graph_src
            assert f'"__{name}": event["data"]' in stream_src

    def test_docstring_declares_the_new_events(self, agent_client):
        ac = agent_client
        doc = inspect.getdoc(ac.call_agent_stream) or ""
        assert '("options"' in doc and '("evidence"' in doc and '("result_set"' in doc and '("refusal"' in doc
