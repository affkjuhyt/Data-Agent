import os
import time
import logging

import litellm

from observability import get_langfuse_client
from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_PROMPTS_DIR     = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM_TEMPLATE = open(os.path.join(_PROMPTS_DIR, "responder_system.txt"),          encoding="utf-8").read()
_OPTIONS_SUFFIX  = open(os.path.join(_PROMPTS_DIR, "responder_options_suffix.txt"),  encoding="utf-8").read()
_LANGUAGE_RULE   = "IMPORTANT — Language: Respond in {LANGUAGE}. Vietnamese messages often contain English technical terms — still respond entirely in Vietnamese in that case."

_LANGUAGE_LABELS = {"vi": "Vietnamese", "en": "English"}


def _to_lc_messages(messages: list[dict]) -> list[dict]:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]


async def responder_node(state: AgentState) -> dict:
    cfg = state["config"]
    intent = state.get("intent", "question")
    language = state.get("language") or "vi"
    lang_label = _LANGUAGE_LABELS.get(language, "Vietnamese")
    system_prompt = _SYSTEM_TEMPLATE.replace("{SYSTEM_PROMPT}", cfg["system_prompt"])
    if intent == "task":
        system_prompt += "\n\n" + _OPTIONS_SUFFIX
    system_prompt += "\n\n" + _LANGUAGE_RULE.replace("{LANGUAGE}", lang_label)

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

    return {"response": full_text}
