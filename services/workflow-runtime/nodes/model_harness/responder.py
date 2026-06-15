import os
import time
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_PROMPTS_DIR     = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM_TEMPLATE = open(os.path.join(_PROMPTS_DIR, "responder_system.txt"),          encoding="utf-8").read()
_OPTIONS_SUFFIX  = open(os.path.join(_PROMPTS_DIR, "responder_options_suffix.txt"),  encoding="utf-8").read()

_LANGUAGE_LABELS = {"vi": "Vietnamese", "en": "English"}


def _to_lc_messages(messages: list[dict]) -> list:
    role_map = {"user": HumanMessage, "assistant": AIMessage}
    return [
        role_map[m["role"]](content=m["content"])
        for m in messages
        if m.get("role") in role_map
    ]


async def responder_node(state: AgentState) -> dict:
    cfg = state["config"]
    intent = state.get("intent", "question")
    language = state.get("language") or "vi"
    lang_label = _LANGUAGE_LABELS.get(language, "Vietnamese")
    system_prompt = (
        _SYSTEM_TEMPLATE
        .replace("{SYSTEM_PROMPT}", cfg["system_prompt"])
        .replace("{LANGUAGE}", lang_label)
    )
    if intent == "task":
        system_prompt += "\n\n" + _OPTIONS_SUFFIX

    if state.get("reranked_chunks"):
        max_chunks = cfg.get("context_max_chunks", 5)
        max_chars  = cfg.get("context_max_chars", 4000)
        chunks = state["reranked_chunks"][:max_chunks]

        parts = []
        total_chars = 0
        for i, c in enumerate(chunks):
            source    = c.get("source", c.get("id", "unknown"))
            score     = c.get("score", c.get("rrf_score", ""))
            score_str = f" | score={score:.3f}" if isinstance(score, float) else ""
            header    = f"[{i+1}] source={source}{score_str}"
            content   = c["content"]
            if total_chars + len(content) > max_chars:
                content = content[:max_chars - total_chars]
            parts.append(f"{header}\n{content}")
            total_chars += len(content)
            if total_chars >= max_chars:
                break

        context = "\n\n---\n\n".join(parts)
        system_prompt = f"{system_prompt}\n\nContext:\n{context}"

    history = _to_lc_messages([
        m for m in state.get("messages", [])
        if m.get("role") in ("user", "assistant")
    ])
    # tránh duplicate nếu query đã có trong message cuối
    if history and isinstance(history[-1], HumanMessage) and history[-1].content == state["query"]:
        history = history[:-1]

    messages = [SystemMessage(content=system_prompt), *history, HumanMessage(content=state["query"])]

    llm = ChatOpenAI(
        model=cfg.get("responder_model", "responder"),
        base_url=f"{_LITELLM_BASE}/v1",
        api_key=_LITELLM_KEY,
        temperature=cfg.get("responder_temperature", 0.2),
        max_tokens=cfg.get("responder_max_tokens", 1000),
        streaming=cfg.get("responder_streaming", True),
    )

    t0 = time.monotonic()
    response = await llm.ainvoke(messages)
    latency_ms = int((time.monotonic() - t0) * 1000)

    full_text = response.content
    usage = response.response_metadata.get("usage", {}) if response.response_metadata else {}
    prompt_tokens     = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    logger.info("responder | latency=%dms prompt=%d completion=%d",
                latency_ms, prompt_tokens, completion_tokens)

    return {"response": full_text}
