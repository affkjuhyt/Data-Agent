import json
import logging
import os
import time

import litellm

from observability import get_langfuse_client
from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_VERDICT_PASS = "PASS"
_VERDICT_FAIL = "FAIL"

_ERROR_TYPES = frozenset({"RETRIEVAL_ERROR", "TOOL_ERROR", "DATA_QUALITY", "PLANNING_ERROR"})

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "judge_system.txt"), encoding="utf-8").read().strip()


async def judge_node(state: AgentState) -> dict:
    t0  = time.monotonic()
    cfg = state.get("config", {})

    max_iter  = cfg.get("loop_max_iterations", 3)
    iteration = state.get("loop_iteration", 0)

    # Không có response → không cần judge
    response = state.get("response", "").strip()
    if not response:
        return _pass(iteration)

    # Đã đạt max iterations
    if iteration >= max_iter:
        logger.warning("judge | max_iterations=%d reached → force PASS", max_iter)
        return _pass(iteration, reason="max_iterations_reached")

    # Build prompt
    reranked  = state.get("reranked_chunks", [])
    tool_errs = state.get("tool_errors", [])

    context_lines = []
    for i, c in enumerate(reranked[:5], 1):
        content = c.get("content", "")
        if content:
            context_lines.append(f"[{i}] {content[:400]}")
    context_text = "\n".join(context_lines) or "(không có context)"

    tool_err_text = json.dumps(tool_errs, ensure_ascii=False) if tool_errs else "(không có lỗi)"

    user_msg = (
        f"CÂU HỎI: {state['query']}\n\n"
        f"CONTEXT ĐÃ RETRIEVE:\n{context_text}\n\n"
        f"TOOL ERRORS:\n{tool_err_text}\n\n"
        f"CÂU TRẢ LỜI CỦA AI:\n{response[:800]}"
    )

    model = cfg.get("judge_model", "openai/judge")
    messages_payload = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_msg},
    ]
    try:
        resp = await litellm.acompletion(
            model=model,
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=messages_payload,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=128,
            timeout=15,
        )
        raw  = resp.choices[0].message.content or ""
        lf = get_langfuse_client()
        if lf:
            usage = resp.usage or {}
            lf.update_current_generation(
                model=model,
                input=messages_payload,
                output=raw,
                usage_details={
                    "input": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
                    "output": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
                },
            )
        data = json.loads(raw)
    except Exception as e:
        logger.warning("judge | LLM call failed: %s → PASS fallback", e)
        return _pass(iteration, reason=f"judge_error:{e}")

    verdict    = data.get("verdict", _VERDICT_PASS)
    error_type = data.get("error_type") if verdict == _VERDICT_FAIL else None
    reason     = data.get("reason", "")

    # Sanitize error_type
    if error_type and error_type not in _ERROR_TYPES:
        logger.warning("judge | unknown error_type=%r → PASS", error_type)
        verdict, error_type = _VERDICT_PASS, None

    latency = int((time.monotonic() - t0) * 1000)
    logger.info("judge | iteration=%d verdict=%s error_type=%s latency=%dms",
                iteration, verdict, error_type, latency)

    if verdict == _VERDICT_FAIL:
        return _fail(iteration, error_type, reason)

    return _pass(iteration, reason=reason)


def _pass(iteration: int, reason: str = "") -> dict:
    return {
        "judge_verdict":     _VERDICT_PASS,
        "judge_error_type":  None,
        "judge_reason":      reason,
        "loop_iteration":    iteration,
    }


def _fail(iteration: int, error_type: str, reason: str) -> dict:
    reset: dict = {"response": ""}

    if error_type == "RETRIEVAL_ERROR":
        reset |= {"kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
                  "rrf_results": [], "reranked_chunks": []}
    elif error_type == "TOOL_ERROR":
        reset |= {"tool_results": [], "tool_errors": [],
                  "rrf_results": [], "reranked_chunks": []}
    elif error_type == "PLANNING_ERROR":
        # Reset toàn bộ downstream của planner — planner sẽ re-emit tool_calls mới
        reset |= {"tool_calls": [], "kb_chunks": [], "mcp_results": [],
                  "tool_results": [], "tool_errors": [],
                  "rrf_results": [], "reranked_chunks": []}

    return {
        "judge_verdict":    _VERDICT_FAIL,
        "judge_error_type": error_type,
        "judge_reason":     reason,
        "loop_iteration":   iteration + 1,
        **reset,
    }
