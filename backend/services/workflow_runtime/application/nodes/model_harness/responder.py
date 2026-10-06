import contextlib
import logging
import os
import re
import time
from typing import Any

import litellm
from langchain_core.callbacks.manager import adispatch_custom_event

from libs.shared import aggregation_check, freshness, untrusted
from services.workflow_runtime.application import result_sets
from services.workflow_runtime.application.nodes.model_harness.intent import guidance
from services.workflow_runtime.application.nodes.model_harness.intent import (
    parse as parse_analysis_type,
)
from services.workflow_runtime.application.runtime_services import llm_call
from services.workflow_runtime.domain.state import AgentState
from services.workflow_runtime.prompts import PROMPTS_DIR

logger = logging.getLogger(__name__)

_AGGREGATION_RULE = (
    "\n\nAggregation risk - a context item may carry an `aggregation-risk:` line. "
    "It means the figure was produced by a query whose shape can multiply it: "
    "a join that matches one row on the left to several on the right duplicates "
    "those rows before the SUM or COUNT runs.\n"
    "- State that the figure is unverified and say why, in one short sentence.\n"
    "- Do NOT restate it as a confirmed total, and do not compute anything "
    "further from it (a share, a growth rate, a per-unit figure) - the error "
    "carries into everything derived from it.\n"
    "- It is a RISK, not a proven error: never say the number is wrong, say "
    "it could not be verified and what would settle it."
)


_TRUNCATION_LABELS = {
    "row_limit": "only the first {rows} row(s) were returned (row cap)",
    "byte_limit": "only the first {rows} row(s) fitted in the result size cap",
    "cell_limit": "the rows are complete but some individual values were shortened",
}

_TRUNCATION_RULE = (
    "\n\nIncomplete results - a context item may carry a `result-truncated:` line. "
    "When one is present:\n"
    "- Say plainly that the data shown is a partial slice, not the full result.\n"
    "- Do NOT compute or state totals, counts, averages, minimums or maximums "
    "over it as if it were complete - an aggregate over a truncated slice is a "
    "wrong number that looks like a right one.\n"
    "- Suggest narrowing the question (a filter, a date range, a GROUP BY) "
    "instead of presenting the partial figures as the answer."
)


def _truncation_note(truncation: Any) -> str:
    """`result-truncated: ...` for a capped result, or "" for a complete one."""
    if not isinstance(truncation, dict):
        return ""
    reason = truncation.get("reason") or ""
    rows = truncation.get("rows_returned", 0)
    parts = []
    label = _TRUNCATION_LABELS.get(reason)
    if label:
        parts.append(label.format(rows=rows))
    elif reason:
        parts.append(f"result was cut short ({reason})")
    else:
        return ""
    cells = truncation.get("cells_truncated") or 0
    if cells and reason != "cell_limit":
        parts.append(f"{cells} oversized value(s) also shortened")
    return "result-truncated: " + "; ".join(parts)


# Appended only when at least one context item carries governance facts —
# instructing the model to disclose an "as of" it has not been given produces an
# invented date, which is worse than the silence this replaces.
_FRESHNESS_RULE = (
    "\n\nData freshness — the context items above may carry a `governance:` line "
    "stating how old the underlying data is. When one is present:\n"
    "- State the 'as of' time alongside the figures, in the user's language.\n"
    "- If it says STALE, warn clearly that the data is past its agreed refresh "
    "window and the figures may not reflect the current state.\n"
    "- If it says UNKNOWN or UNVERIFIED, say the freshness could not be "
    "confirmed. Do not present unverified data as current.\n"
    "- Never invent, round or extrapolate an 'as of' time that is not stated "
    "there, and never describe data as up-to-date on the basis of the question "
    "having just been asked."
)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_SYSTEM_TEMPLATE  = open(os.path.join(PROMPTS_DIR, "responder_system.txt"),           encoding="utf-8").read()
_OPTIONS_SUFFIX   = open(os.path.join(PROMPTS_DIR, "responder_options_suffix.txt"),   encoding="utf-8").read()
_CITATION_SUFFIX  = open(os.path.join(PROMPTS_DIR, "responder_citation_suffix.txt"),  encoding="utf-8").read()
_LANGUAGE_RULE    = "IMPORTANT — Language: Respond in {LANGUAGE}. Vietnamese messages often contain English technical terms — still respond entirely in Vietnamese in that case."

_LANGUAGE_LABELS = {"vi": "Vietnamese", "en": "English"}

_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

# Custom event carrying the answer as it is written. conversation_runner.py
# shows it live and withdraws it if the judge or the output guardrail rejects
# the finished answer.
RESPONDER_TOKEN_EVENT = "responder_token"


async def _stream_token(text: str) -> None:
    # Outside a graph run there is no parent run to attach the event to:
    # nobody is listening, and the complete answer is still returned.
    with contextlib.suppress(RuntimeError):
        await adispatch_custom_event(RESPONDER_TOKEN_EVENT, text)

def _citation_type(chunk: dict[str, Any]) -> str:
    """kb | mcp | db | web | tool — dùng để FE tô màu badge theo loại nguồn.
    rrf_sources do rrf_ranking_node gắn (giữ nguyên qua reranker vì reranker
    chỉ spread **item, không xoá field lạ)."""
    category: str = (chunk.get("rrf_sources") or ["kb"])[0]
    if category != "tool":
        return category
    chunk_id = chunk.get("id", "")
    if chunk_id.startswith("web_") or chunk_id.startswith("wiki_"):
        return "web"
    return "tool"


def _to_lc_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]


async def responder_node(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    intent = state.get("intent", "question")
    language = state.get("language") or "vi"
    lang_label = _LANGUAGE_LABELS.get(language, "Vietnamese")
    system_prompt = _SYSTEM_TEMPLATE.replace("{SYSTEM_PROMPT}", cfg["system_prompt"])
    if intent == "task":
        system_prompt += "\n\n" + _OPTIONS_SUFFIX

    # Answer shaping per analysis type — and, for the two kinds this platform
    # cannot actually do, an explicit instruction to say so. Without it a
    # forecast question gets a number invented from the model's own priors,
    # indistinguishable in the answer from one computed off real data.
    analysis_type = parse_analysis_type(state.get("analysis_type"))
    type_guidance = guidance(analysis_type)
    if type_guidance:
        system_prompt += "\n\n" + type_guidance

    # A deterministic rule found a structural gap (no period on a time-bound
    # metric, a metric name covering two different formulas). Nothing was
    # executed, so there is no evidence to answer from — ask, and say why.
    clarifications = state.get("clarifications") or []
    if clarifications:
        asks = "\n".join(f"- {c.get('text', '')}" for c in clarifications)
        options = [o for c in clarifications for o in (c.get("options") or [])]
        system_prompt += (
            "\n\nChưa thể trả lời vì thiếu thông tin. HÃY HỎI LẠI đúng những ý sau, "
            "ngắn gọn, bằng ngôn ngữ của người dùng. TUYỆT ĐỐI không đoán và không "
            f"đưa ra bất kỳ con số nào:\n{asks}"
        )
        if options:
            system_prompt += "\nGợi ý lựa chọn: " + ", ".join(options)

    # A gap already asked about is not asked twice (ambiguity.settle): its
    # default reading was applied instead, and the answer has to say so — "all
    # history" otherwise reads exactly like the period the user meant.
    assumptions = [] if clarifications else (state.get("assumptions") or [])
    if assumptions:
        notes = "\n".join(f"- {a.get('detail', '')}" for a in assumptions)
        system_prompt += (
            "\n\nNgười dùng chưa chọn một số thông tin nên câu trả lời dùng cách hiểu mặc định. "
            f"NÊU RÕ ngắn gọn các giả định sau ngay đầu câu trả lời:\n{notes}"
        )

    system_prompt += "\n\n" + _LANGUAGE_RULE.replace("{LANGUAGE}", lang_label)

    # Deterministic "why trust this" payload for the UI — built alongside the
    # structured question (with its options) instead of only the prose.
    evidence: dict[str, Any] = {
        "runs": [],
        "clarifications": clarifications,
        # Assumptions first: they qualify the whole answer, unit warnings one figure.
        "warnings": list(assumptions),
    }

    chunk_meta: dict[int, dict[str, Any]] = {}
    tables, chunks = result_sets.context_items(state, cfg)
    used_tables: list[dict[str, Any]] = []
    if tables or chunks:
        max_chars = cfg.get("context_max_chars", 4000)
        max_rows  = result_sets.context_max_rows(cfg)

        parts = []
        has_governance = False
        has_truncation = False
        has_aggregation_risk = False

        for i, rs in enumerate(tables, 1):
            prov = rs.get("provenance") or {}
            source = untrusted.one_line(prov.get("connection_id") or "result")
            header = f"[{i}] source={source} | result={rs.get('result_id')} | trust={rs.get('trust')}"
            # Freshness/owner/classification/lineage for the tables behind this
            # result, on its own line above the rows rather than mixed into
            # them: a provenance key inside the JSON would read as another
            # column of the result.
            governance = freshness.describe(prov.get("freshness"))
            if governance:
                header = f"{header}\ngovernance: {governance}"
                has_governance = True
            # A capped result is not a smaller answer to the same question. A
            # total over the rows that fitted looks exactly like the real total,
            # so the model is told the rows are a prefix and instructed below
            # not to aggregate over them as if they were everything.
            truncation = _truncation_note(prov.get("truncation"))
            if truncation:
                header = f"{header}\n{truncation}"
                has_truncation = True
            text = result_sets.render(rs, max_rows)
            parts.append(f"{header}\n{text}")
            chunk_meta[i] = {
                "index":   i,
                "source":  source,
                "type":    "db",
                "score":   None,
                "snippet": text[:200],
                "url":     None,
                "doc_id":  None,
                "page":    None,
                "result_id": rs.get("result_id"),
                "provenance": prov.get("freshness") or None,
                "truncation": prov.get("truncation") or None,
                "aggregation": prov.get("aggregation") or None,
            }
            evidence["runs"].append(result_sets.evidence_run(rs, citation_index=i))
            used_tables.append(result_sets.frame(rs, citation_index=i))

        total_chars = 0
        for i, c in enumerate(chunks, len(tables) + 1):
            source    = untrusted.one_line(c.get("source") or c.get("id", "unknown"))
            score     = c.get("score", c.get("rrf_score", ""))
            score_str = f" | score={score:.3f}" if isinstance(score, float) else ""
            header    = f"[{i}] source={source}{score_str}"
            content   = c["content"]

            if total_chars + len(content) > max_chars:
                content = content[:max_chars - total_chars]
            parts.append(f"{header}\n{content}")
            total_chars += len(content)

            ctype = _citation_type(c)
            chunk_meta[i] = {
                "index":   i,
                "source":  source,
                "type":    ctype,
                "score":   score if isinstance(score, float) else None,
                "snippet": content[:200],
                "url":     c.get("url") or None,
                "doc_id":  c.get("doc_id") or None if ctype == "kb" else None,
                # Trang chứa đoạn được trích, để bấm vào citation là mở đúng
                # chỗ (#page=N) thay vì mở trang đầu của một bản tin 60 trang.
                # None với tài liệu nạp trước khi toạ độ được lưu.
                "page":    c.get("page") if ctype == "kb" else None,
                "provenance": None,
                "truncation": None,
                "aggregation": None,
            }

            if total_chars >= max_chars:
                break

        # Units across the whole context, not per item: a mismatch only exists
        # between two figures. Unlike the fan-out check this is exact - the unit
        # comes from an approved metric contract, so combining VND with USD is
        # not a risk to weigh but an error to state.
        unit_conflict = aggregation_check.check_units([
            ((rs.get("provenance") or {}).get("metric") or {}).get("unit", "")
            for rs in tables
        ])
        if unit_conflict:
            parts.append(f"aggregation-risk: {unit_conflict.kind}: {unit_conflict.detail}")
            has_aggregation_risk = True
            evidence["warnings"].append(unit_conflict.to_dict())

        # Everything in `parts` came out of a KB document, a SQL result or a
        # tool payload - none of it written by this platform or typed by the
        # caller. Fenced and labelled so a chunk saying "ignore the above,
        # you are now..." reads as a quote inside the evidence instead of as
        # the system prompt resuming. wrap() also defangs a chunk shipping
        # its own closing tag to end the fence early.
        context = untrusted.wrap("\n\n---\n\n".join(parts))
        system_prompt = f"{system_prompt}\n\nContext:\n{context}{untrusted.RULE}\n\n{_CITATION_SUFFIX}"
        if has_governance:
            system_prompt += _FRESHNESS_RULE
        if has_truncation:
            system_prompt += _TRUNCATION_RULE
        if has_aggregation_risk:
            system_prompt += _AGGREGATION_RULE

    injected_metadata = state.get("injected_metadata")
    if injected_metadata:
        meta_lines = "\n".join(f"- {k}: {v}" for k, v in injected_metadata.items())
        system_prompt = f"{system_prompt}\n\nMetadata:\n{meta_lines}"

    history = _to_lc_messages(state.get("messages", []))
    # tránh duplicate nếu query đã có trong message cuối
    if history and history[-1]["role"] == "user" and history[-1]["content"] == state["query"]:
        history = history[:-1]

    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": state["query"]},
    ]

    model = cfg.get("responder_model", "responder")
    t0 = time.monotonic()
    response = await llm_call.acompletion(
        "llm:responder", litellm.acompletion,
        on_token=_stream_token,
        model=model,
        api_base=_LITELLM_BASE,
        api_key=_LITELLM_KEY,
        messages=messages,
        temperature=cfg.get("responder_temperature", 0.2),
        max_tokens=cfg.get("responder_max_tokens", 1000),
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    full_text = response.choices[0].message.content or ""
    usage = response.usage or {}
    prompt_tokens     = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)

    logger.info("responder | latency=%dms prompt=%s completion=%s",
                latency_ms, prompt_tokens, completion_tokens)

    # Chỉ giữ citation THỰC SỰ xuất hiện trong câu trả lời (model có thể không
    # cite hết context được cấp) — theo đúng thứ tự xuất hiện lần đầu trong text.
    citations = []
    seen = set()
    for m in _CITATION_MARKER_RE.finditer(full_text):
        n = int(m.group(1))
        if n in chunk_meta and n not in seen:
            seen.add(n)
            citations.append(chunk_meta[n])

    # `_result_sets` is not a state key — LangGraph may drop it on merge, as it
    # does tool_executor's `_tool_traces`. conversation_runner reads it when
    # available and falls back to the responder input's result_sets channel,
    # then sends one `__result_set` frame per table used by the answer.
    return {"response": full_text, "citations": citations, "evidence": evidence,
            "_result_sets": used_tables}
