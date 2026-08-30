"""Getting an answer out of the system under test, for the online tier.

Two ways in, because they answer different questions and the corpus needs the
first one:

  * **`answer_with_contexts`** — the shipped responder prompt, the shipped
    analysis-type guidance, and the case's own contexts as the retrieved
    sources. This isolates the ANSWERING step: given exactly these sources, does
    the answer stay inside them? Retrieval quality is a separate concern with a
    separate corpus, and mixing them produces a hallucination rate that moves
    when the index is reranked.

  * **`ask_live_agent`** — the whole pipeline over SSE, for cases with no
    contexts of their own. End to end, including retrieval.

Both reuse what ships: the prompt template from backend/services/workflow_runtime/
prompts, and the real planner for the analysis type, so the guidance a forecast
question gets here is the guidance it gets in production. An eval that wrote its
own prompt would measure a system nobody runs.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from model.eval import _paths

_LITELLM_BASE = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY = os.getenv("LITELLM_API_KEY", "anything")
_RESPONDER_MODEL = os.getenv("EVAL_RESPONDER_MODEL", "responder")

# The system prompt the agent builder fills in per agent. Neutral here so the
# metric reflects the platform's own grounding rules rather than one tenant's
# persona.
_BASE_SYSTEM_PROMPT = (
    "Bạn là trợ lý dữ liệu. Chỉ trả lời dựa trên nguồn được cung cấp. "
    "Nếu nguồn không chứa câu trả lời, hãy nói rõ là không có trong nguồn."
)


def _prompts_dir():
    return _paths.WORKFLOW_RUNTIME / "prompts"


def _responder_system(analysis_type: str) -> str:
    """The shipped responder system prompt, plus the shipped per-type guidance."""
    _paths.ensure(_paths.WORKFLOW_RUNTIME)
    template = (_prompts_dir() / "responder_system.txt").read_text(encoding="utf-8")
    system = template.replace("{SYSTEM_PROMPT}", _BASE_SYSTEM_PROMPT)

    from nodes.model_harness.intent import guidance
    from nodes.model_harness.intent import parse as parse_analysis_type

    extra = guidance(parse_analysis_type(analysis_type))
    return system + ("\n\n" + extra if extra else "")


async def answer_with_contexts(question: str, contexts: list[str], analysis_type: str = "other") -> str:
    import litellm

    sources = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    response = await litellm.acompletion(
        model=_RESPONDER_MODEL,
        api_base=_LITELLM_BASE,
        api_key=_LITELLM_KEY,
        messages=[
            {"role": "system", "content": _responder_system(analysis_type)},
            {"role": "user", "content": f"Nguồn:\n{sources}\n\nCâu hỏi: {question}"},
        ],
        temperature=0,
        max_tokens=512,
        timeout=60,
    )
    return response.choices[0].message.content or ""


async def ask_live_agent(agent_url: str, agent_id: str, tenant_id: str, question: str) -> str:
    """The whole pipeline, over the same SSE endpoint the product uses."""
    payload = {"query": question, "agent_id": agent_id}
    headers = {"X-Tenant-ID": tenant_id, "Content-Type": "application/json"}
    chunks: list[str] = []

    async with (
        httpx.AsyncClient(timeout=120) as client,
        client.stream(
            "POST", f"{agent_url}/api/conversations/run", json=payload, headers=headers,
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw.startswith("[DONE]") or raw.startswith("[ERROR]"):
                break
            try:
                piece = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(piece, str):
                chunks.append(piece)

    return "".join(chunks)


async def ask(case: dict[str, Any], *, agent_url: str, agent_id: str, tenant_id: str) -> str:
    """Answer one grounding case the way production would.

    Classified first by the REAL planner, because the answer shaping a forecast
    question receives is a consequence of that classification — evaluating the
    responder without it would measure a responder that was never told what kind
    of question it had.
    """
    contexts = case.get("contexts") or []
    if not contexts:
        return await ask_live_agent(agent_url, agent_id, tenant_id, case["question"])

    from model.eval.scorers.intent import planner_classifier

    try:
        classified = await planner_classifier(case["question"])
        analysis_type = classified.get("analysis_type", "other")
    # A classifier outage must not silently turn into "no guidance", which would
    # make the forecast cases look better than the system is — so this catches
    # everything and the case is still answered, ungrounded guidance and all.
    except Exception:  # noqa: BLE001
        analysis_type = "other"

    return await answer_with_contexts(case["question"], contexts, analysis_type)
