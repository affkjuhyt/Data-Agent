import json
import logging
import os
import time
from typing import Any

import litellm

from libs.indicator.numeric import readings
from libs.indicator.verifier import EvidenceItem, Verdict, verify
from libs.shared import untrusted
from services.workflow_runtime.application import result_sets
from services.workflow_runtime.application.judge_routing import ERROR_CLASSES, plan_signature
from services.workflow_runtime.application.runtime_services import llm_call
from services.workflow_runtime.domain.refusals import NoDataError, NoJoinPathError
from services.workflow_runtime.domain.state import AgentState
from services.workflow_runtime.prompts import PROMPTS_DIR

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_VERDICT_PASS = "PASS"
_VERDICT_FAIL = "FAIL"

_ERROR_TYPES = frozenset({"DATA_QUALITY", "PLANNING_ERROR", "GROUNDING_ERROR"})
_ERROR_CLASS_BY_TYPE = {
    "PLANNING_ERROR": "PLAN_LOGIC",
    "GROUNDING_ERROR": "EVIDENCE_INSUFFICIENT",
    "DATA_QUALITY": "MATERIALIZATION",
}

_SYSTEM = open(os.path.join(PROMPTS_DIR, "judge_system.txt"), encoding="utf-8").read().strip()


async def judge_node(state: AgentState) -> dict[str, Any]:
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

    tool_errs = state.get("tool_errors", [])

    # Same budget the responder answered from (responder.py): the judge used to
    # see only 400 chars per item, so a 30-row SQL result was cut to its first
    # rows and every figure after them read as invented — GROUNDING_ERROR on a
    # correct answer, then a wasted responder retry. Reproduced live: 3/3 FAIL
    # truncated, 0/3 with the responder's context.
    max_chars  = cfg.get("context_max_chars", 4000)
    max_rows   = result_sets.context_max_rows(cfg)
    tables, chunks_for_judge = result_sets.context_items(state, cfg)
    rendered_tables = [result_sets.render(rs, max_rows) for rs in tables]
    context_lines = [f"[{i}] {text}" for i, text in enumerate(rendered_tables, 1)]
    total_chars = 0
    for i, c in enumerate(chunks_for_judge, len(tables) + 1):
        content = c.get("content", "")
        if not content:
            continue
        if total_chars + len(content) > max_chars:
            content = content[:max_chars - total_chars]
        context_lines.append(f"[{i}] {content}")
        total_chars += len(content)
        if total_chars >= max_chars:
            break
    # Same content the responder answered from, so the same fence: a chunk
    # that talks the judge into PASS is a cheaper attack than one that talks
    # the responder into leaking, and it clears the way for the next.
    context_text = untrusted.wrap("\n".join(context_lines)) if context_lines else "(không có context)"

    # Kiểm tra tất định TRƯỚC khi hỏi model (NC-ADR-T5, docs/v2/12). Con số, trích dẫn và
    # vintage là chỗ hệ thống phải chắc chắn, mà hỏi một model thì lại là đưa
    # tính bất định vào đúng chỗ đó — judge_system.txt vẫn so khớp chuỗi nên
    # "1284" lọt qua khi nguồn ghi "128,4". Trượt ở đây là GROUNDING_ERROR
    # ngay, không tốn một lượt gọi LLM và không thể tranh cãi.
    numeric_verdict = verify(response, _table_evidence(tables, rendered_tables) + _evidence(chunks_for_judge))
    verifier_report = {"verifier": _report(numeric_verdict)}
    if not numeric_verdict.passed:
        detail = "; ".join(
            f"{c.name}: {c.detail}" + (f" ({', '.join(c.offending)})" if c.offending else "")
            for c in numeric_verdict.failures
        )
        logger.info("judge | deterministic verifier failed: %s", detail)
        return _fail(
            iteration, "GROUNDING_ERROR", detail, "EVIDENCE_INSUFFICIENT", state=state
        ) | verifier_report

    tool_err_text = json.dumps(tool_errs, ensure_ascii=False) if tool_errs else "(không có lỗi)"

    user_msg = (
        f"CÂU HỎI: {state['query']}\n\n"
        f"CONTEXT ĐÃ RETRIEVE:\n{context_text}\n\n"
        f"TOOL ERRORS:\n{tool_err_text}\n\n"
        f"CÂU TRẢ LỜI CỦA AI:\n{response[:4000]}"
    )

    model = cfg.get("judge_model", "openai/judge")
    messages_payload = [
        {"role": "system", "content": _SYSTEM + untrusted.RULE},
        {"role": "user",   "content": user_msg},
    ]
    try:
        resp = await llm_call.acompletion(
            "llm:judge", litellm.acompletion,
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
        data = json.loads(raw)
    except Exception as e:
        logger.warning("judge | LLM call failed: %s → PASS fallback", e)
        return _pass(iteration, reason=f"judge_error:{e}") | verifier_report

    verdict    = data.get("verdict", _VERDICT_PASS)
    error_type = data.get("error_type") if verdict == _VERDICT_FAIL else None
    error_class = data.get("error_class") if verdict == _VERDICT_FAIL else None
    reason     = data.get("reason", "")

    # Sanitize error_type
    if error_type and error_type not in _ERROR_TYPES:
        logger.warning("judge | unknown error_type=%r → PASS", error_type)
        verdict, error_type = _VERDICT_PASS, None
    if error_class not in ERROR_CLASSES:
        error_class = _ERROR_CLASS_BY_TYPE.get(error_type or "")

    if error_class == "DATA_MISSING_DIRECT":
        raise NoDataError("judge classified the direct query as missing data")
    if error_class == "DATA_MISSING_INDIRECT":
        raise NoJoinPathError("judge classified the indirect retrieval as missing a join path")

    latency = int((time.monotonic() - t0) * 1000)
    logger.info("judge | iteration=%d verdict=%s error_type=%s latency=%dms",
                iteration, verdict, error_type, latency)

    if verdict == _VERDICT_FAIL:
        return _fail(iteration, error_type, reason, error_class, state=state) | verifier_report

    return _pass(iteration, reason=reason) | verifier_report


def _table_evidence(tables: list[dict[str, Any]], rendered: list[str]) -> list[EvidenceItem]:
    """The figures each result set entitles the answer to state: its preview
    rows and its stats over every returned row — so a total read off `sum` is
    grounded although no single row shows it. Cited by result id: the reader
    opens the table, not a document."""
    return [
        EvidenceItem(
            readings=tuple(reading for _, candidates in readings(text) for reading in candidates),
            doc_id="",
            page=None,
            source_ref=rs.get("result_id") or None,
        )
        for rs, text in zip(tables, rendered, strict=True)
    ]


def _evidence(chunks: list[dict[str, Any]]) -> list[EvidenceItem]:
    """The figures each retrieved chunk entitles the answer to state.

    One item per chunk, in the order the responder numbered them, so `[n]` in
    the answer indexes the same document the reader will be sent to.
    """
    return [
        EvidenceItem(
            readings=tuple(
                reading
                for _, candidates in readings(chunk.get("content", ""))
                for reading in candidates
            ),
            doc_id=chunk.get("doc_id") or "",
            page=chunk.get("page"),
            source_ref=_source_ref(chunk),
        )
        for chunk in chunks
    ]


def _source_ref(chunk: dict[str, Any]) -> str | None:
    """What a citation of a non-KB chunk (warehouse result, web page, tool
    output) opens, the way responder.py's citation shows it. A KB chunk is
    cited by its doc_id alone: one without it really does resolve to nothing."""
    # Same category responder._citation_type starts from; a chunk without one is KB.
    if (chunk.get("rrf_sources") or ["kb"])[0] == "kb":
        return None
    return chunk.get("url") or chunk.get("id") or None


def _report(verdict: Verdict) -> dict[str, Any]:
    """Structured result for the UI's VerifierBadge — one entry per check."""
    return {
        "passed": verdict.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail,
             "offending": list(c.offending)}
            for c in verdict.checks
        ],
    }


def _pass(iteration: int, reason: str = "") -> dict[str, Any]:
    return {
        "judge_verdict":     _VERDICT_PASS,
        "judge_error_type":  None,
        "judge_reason":      reason,
        "loop_iteration":    iteration,
        "error_class":       None,
    }


def _fail(
    iteration: int,
    error_type: str | None,
    reason: str,
    error_class: str | None = None,
    *,
    state: AgentState | None = None,
) -> dict[str, Any]:
    reset: dict[str, Any] = {"response": ""}

    if error_type == "PLANNING_ERROR":
        # Reset toàn bộ downstream của planner — planner sẽ re-emit tool_calls mới
        reset |= {"tool_calls": [], "kb_chunks": [], "mcp_results": [],
                  "tool_results": [], "tool_errors": [],
                  "result_sets": None, "db_error": None,
                  "rrf_results": [], "reranked_chunks": []}
    # GROUNDING_ERROR: chỉ reset "response" (default ở trên) — context/data đã
    # đúng, chỉ có bước diễn giải của responder bị sai, không cần tra lại.
    # DATA_QUALITY: cũng chỉ reset response — hiếm gặp, không có recovery
    # action riêng, để planner/responder tự thấy judge_reason ở lần sau.

    previous = state or {}
    return {
        "judge_verdict":    _VERDICT_FAIL,
        "judge_error_type": error_type,
        "judge_reason":     reason,
        "loop_iteration":   iteration + 1,
        "error_class":       error_class,
        "error_class_history": [*previous.get("error_class_history", []), error_class]
        if error_class else list(previous.get("error_class_history", [])),
        "attempted_plans": [
            *previous.get("attempted_plans", []),
            plan_signature(previous.get("tool_calls", [])),
        ] if error_class else list(previous.get("attempted_plans", [])),
        **reset,
    }
