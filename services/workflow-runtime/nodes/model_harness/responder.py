from typing import Any
import os
import re
import time
import logging

import litellm

from nodes.model_harness.intent import guidance, parse as parse_analysis_type
from observability import get_langfuse_client
from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_PROMPTS_DIR      = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM_TEMPLATE  = open(os.path.join(_PROMPTS_DIR, "responder_system.txt"),           encoding="utf-8").read()
_OPTIONS_SUFFIX   = open(os.path.join(_PROMPTS_DIR, "responder_options_suffix.txt"),   encoding="utf-8").read()
_CITATION_SUFFIX  = open(os.path.join(_PROMPTS_DIR, "responder_citation_suffix.txt"),  encoding="utf-8").read()
_LANGUAGE_RULE    = "IMPORTANT — Language: Respond in {LANGUAGE}. Vietnamese messages often contain English technical terms — still respond entirely in Vietnamese in that case."

_LANGUAGE_LABELS = {"vi": "Vietnamese", "en": "English"}

_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")


def _citation_type(chunk: dict) -> str:
    """kb | mcp | db | web | tool — dùng để FE tô màu badge theo loại nguồn.
    rrf_sources do rrf_ranking_node gắn (giữ nguyên qua reranker vì reranker
    chỉ spread **item, không xoá field lạ)."""
    category = (chunk.get("rrf_sources") or ["kb"])[0]
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

    system_prompt += "\n\n" + _LANGUAGE_RULE.replace("{LANGUAGE}", lang_label)

    chunk_meta: dict[int, dict] = {}
    if state.get("reranked_chunks"):
        max_chunks = cfg.get("context_max_chunks", 5)
        max_chars  = cfg.get("context_max_chars", 4000)
        chunks = state["reranked_chunks"][:max_chunks]

        parts = []
        total_chars = 0
        for i, c in enumerate(chunks):
            source    = c.get("source") or c.get("id", "unknown")
            score     = c.get("score", c.get("rrf_score", ""))
            score_str = f" | score={score:.3f}" if isinstance(score, float) else ""
            header    = f"[{i+1}] source={source}{score_str}"
            content   = c["content"]
            if total_chars + len(content) > max_chars:
                content = content[:max_chars - total_chars]
            parts.append(f"{header}\n{content}")
            total_chars += len(content)

            ctype = _citation_type(c)
            chunk_meta[i + 1] = {
                "index":   i + 1,
                "source":  source,
                "type":    ctype,
                "score":   score if isinstance(score, float) else None,
                "snippet": content[:200],
                "url":     c.get("url") or None,
                "doc_id":  c.get("doc_id") or None if ctype == "kb" else None,
            }

            if total_chars >= max_chars:
                break

        context = "\n\n---\n\n".join(parts)
        system_prompt = f"{system_prompt}\n\nContext:\n{context}\n\n{_CITATION_SUFFIX}"

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
    response = await litellm.acompletion(
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

    lf = get_langfuse_client()
    if lf:
        lf.update_current_generation(
            model=model,
            input=messages,
            output=full_text,
            usage_details={"input": prompt_tokens, "output": completion_tokens},
        )

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

    return {"response": full_text, "citations": citations}
