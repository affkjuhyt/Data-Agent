from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import litellm

import config
from observability import get_langfuse_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / ".." / "prompts"


@lru_cache(maxsize=1)
def _system() -> str:
    return (_PROMPTS_DIR / "summary_system.txt").read_text(encoding="utf-8").strip()


async def summarize(original_query: str, agent_outputs: list[str]) -> str:
    """
    1 LLM call cuối: nhận query gốc + tất cả outputs từ các agent tasks
    → trả final response cho user.
    """
    if not agent_outputs:
        return "Không có kết quả từ các agents."

    outputs_block = "\n\n".join(
        f"### Kết quả {i + 1}\n{output}"
        for i, output in enumerate(agent_outputs)
        if output.strip()
    )
    if not outputs_block:
        return "Không có kết quả từ các agents."

    user_message = (
        f"## Câu hỏi của người dùng\n{original_query}\n\n"
        f"## Kết quả từ các agents\n{outputs_block}\n\n"
        "Hãy tổng hợp các kết quả trên và trả lời câu hỏi của người dùng."
    )
    messages_payload = [
        {"role": "system", "content": _system()},
        {"role": "user", "content": user_message},
    ]

    resp = await litellm.acompletion(
        model=config.SUMMARY_MODEL,
        api_base=config.LITELLM_BASE_URL,
        api_key=config.LITELLM_API_KEY,
        messages=messages_payload,
        temperature=0.3,
        max_tokens=1024,
    )

    answer = resp.choices[0].message.content
    lf = get_langfuse_client()
    if lf:
        usage = resp.usage or {}
        lf.update_current_generation(
            model=config.SUMMARY_MODEL,
            input=messages_payload,
            output=answer,
            usage_details={
                "input": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
                "output": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
            },
        )
    logger.info("summary | query_len=%d outputs=%d ans_len=%d",
                len(original_query), len(agent_outputs), len(answer))
    return answer


async def summarize_stream(original_query: str, agent_outputs: list[str]):
    """
    Async generator — yield từng text token từ LLM synthesis.
    """
    if not agent_outputs:
        yield "Không có kết quả từ các agents."
        return

    outputs_block = "\n\n".join(
        f"### Kết quả {i + 1}\n{output}"
        for i, output in enumerate(agent_outputs)
        if output.strip()
    )
    if not outputs_block:
        yield "Không có kết quả từ các agents."
        return

    user_message = (
        f"## Câu hỏi của người dùng\n{original_query}\n\n"
        f"## Kết quả từ các agents\n{outputs_block}\n\n"
        "Hãy tổng hợp các kết quả trên và trả lời câu hỏi của người dùng."
    )
    messages_payload = [
        {"role": "system", "content": _system()},
        {"role": "user", "content": user_message},
    ]

    resp = await litellm.acompletion(
        model=config.SUMMARY_MODEL,
        api_base=config.LITELLM_BASE_URL,
        api_key=config.LITELLM_API_KEY,
        messages=messages_payload,
        temperature=0.3,
        max_tokens=1024,
        stream=True,
        stream_options={"include_usage": True},
    )

    buffer: list[str] = []
    usage = None
    async for chunk in resp:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        token = chunk.choices[0].delta.content if chunk.choices else None
        if token:
            buffer.append(token)
            yield token

    lf = get_langfuse_client()
    if lf:
        usage = usage or {}
        lf.update_current_generation(
            model=config.SUMMARY_MODEL,
            input=messages_payload,
            output="".join(buffer),
            usage_details={
                "input": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
                "output": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
            },
        )
