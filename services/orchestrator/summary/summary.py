from __future__ import annotations

from typing import cast
import json
import logging
from functools import lru_cache
from pathlib import Path

import litellm

import config
from libs.shared import untrusted
from observability import get_langfuse_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / ".." / "prompts"


@lru_cache(maxsize=1)
def _system() -> str:
    return (_PROMPTS_DIR / "summary_system.txt").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _synthesis_judge_system() -> str:
    return (_PROMPTS_DIR / "synthesis_judge_system.txt").read_text(encoding="utf-8").strip()


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

    # An agent's answer is not platform text: it was written by a model that
    # had retrieved documents and tool output in its own context, so a poisoned
    # chunk one hop upstream arrives here as fluent prose the synthesiser would
    # otherwise read as direction.
    user_message = (
        f"## Câu hỏi của người dùng\n{original_query}\n\n"
        f"## Kết quả từ các agents\n{untrusted.wrap(outputs_block, tag='untrusted_results')}\n\n"
        "Hãy tổng hợp các kết quả trên và trả lời câu hỏi của người dùng."
    )
    messages_payload = [
        {"role": "system", "content": _system() + untrusted.RULE},
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
    return cast(str, answer)


async def synthesis_judge_check(original_query: str, agent_outputs: list[str], text: str) -> tuple[bool, str]:
    """Judge cho bước TỔNG HỢP multi-agent — không đánh giá lại từng agent
    (đã tự judge riêng context của nó), chỉ check bước GỘP có bị lệch/mâu
    thuẫn không. Trả về (passed, reason) — fail-open giống judge.py bên
    workflow-runtime (mặc định PASS nếu bản thân lệnh gọi LLM lỗi), khác hẳn
    guardrail (fail-closed) vì đây là Judge, không phải Guardrail."""
    outputs_block = "\n\n".join(
        f"### Kết quả {i + 1}\n{output}"
        for i, output in enumerate(agent_outputs)
        if output.strip()
    )
    messages_payload = [
        {"role": "system", "content": _synthesis_judge_system()},
        {"role": "user", "content": (
            f"CÂU HỎI: {original_query}\n\n"
            f"KẾT QUẢ TỪNG AGENT:\n{outputs_block or '(không có)'}\n\n"
            f"CÂU TỔNG HỢP CẦN CHẤM:\n{text}"
        )},
    ]
    try:
        resp = await litellm.acompletion(
            model=config.SYNTHESIS_JUDGE_MODEL,
            api_base=config.LITELLM_BASE_URL,
            api_key=config.LITELLM_API_KEY,
            messages=messages_payload,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=128,
            timeout=15,
        )
        raw = resp.choices[0].message.content or ""
        lf = get_langfuse_client()
        if lf:
            usage = resp.usage or {}
            lf.update_current_generation(
                model=config.SYNTHESIS_JUDGE_MODEL,
                input=messages_payload,
                output=raw,
                usage_details={
                    "input": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
                    "output": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
                },
            )
        data = json.loads(raw or "{}")
    except Exception as e:
        logger.warning("synthesis_judge | LLM call failed: %s → PASS fallback", e)
        return True, ""

    if data.get("verdict") == "FAIL":
        return False, data.get("reason", "")
    return True, ""
