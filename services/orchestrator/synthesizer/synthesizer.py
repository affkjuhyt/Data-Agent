from __future__ import annotations

import logging

import litellm

import config

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a helpful assistant. You are given a user question and the outputs "
    "from multiple AI agents that each handled part of the task. "
    "Synthesize all results into one clear, coherent final answer for the user. "
    "Be concise and directly address the user's original question."
)


async def synthesize(original_query: str, agent_outputs: list[str]) -> str:
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

    user_message = (
        f"## Câu hỏi của người dùng\n{original_query}\n\n"
        f"## Kết quả từ các agents\n{outputs_block}\n\n"
        "Hãy tổng hợp các kết quả trên và trả lời câu hỏi của người dùng."
    )

    resp = await litellm.acompletion(
        model=config.SYNTHESIZER_MODEL,
        api_base=config.LITELLM_BASE_URL,
        api_key=config.LITELLM_API_KEY,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        max_tokens=1024,
    )

    answer = resp.choices[0].message.content
    logger.info("synthesizer | query_len=%d outputs=%d ans_len=%d",
                len(original_query), len(agent_outputs), len(answer))
    return answer
