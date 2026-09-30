from typing import Any
import functools
import logging
import os
import time
import unicodedata

from libs.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check

_REGEX_TIMEOUT: float | None
try:
    import regex as re_lib
    _REGEX_TIMEOUT = 0.1
except ImportError:
    import re as re_lib
    _REGEX_TIMEOUT = None

logger = logging.getLogger(__name__)

_MAX_CHECK_CHARS = 2000
_HISTORY_TURNS = 6  # số lượt hội thoại gần nhất đưa vào ngữ cảnh check input

# Khi lớp LLM check hỏng (timeout, model down, JSON rác) mà regex cũng không
# bắt được gì: mặc định cho qua (ưu tiên availability) hay chặn (ưu tiên an
# toàn). Đặt GUARDRAIL_FAIL_CLOSED=true cho tenant/triển khai coi việc lọt một
# prompt injection tệ hơn việc từ chối một câu hỏi hợp lệ.
_ENV_FAIL_CLOSED = os.environ.get("GUARDRAIL_FAIL_CLOSED", "false").strip().lower() in ("1", "true", "yes", "on")

_DEGRADED_MESSAGE = "Hệ thống kiểm duyệt tạm thời không khả dụng, vui lòng thử lại."


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re_lib.sub(r"\s+", " ", text).strip()
    return text.lower()


@functools.lru_cache(maxsize=64)
def _compile_patterns(patterns_tuple: tuple[Any, ...]) -> list[Any]:
    compiled = []
    for pid, p, confidence in patterns_tuple:
        try:
            compiled.append((pid, p, confidence, re_lib.compile(p, re_lib.IGNORECASE)))
        except re_lib.error as e:
            logger.warning("guardrail | invalid pattern skipped id=%s error=%s", pid, e)
    return compiled


def _regex_check(text: str, patterns: list[Any]) -> tuple[bool, str, dict[str, Any] | None]:
    text = _normalize(text)[:_MAX_CHECK_CHARS]
    patterns_tuple = tuple((item["id"], item["pattern"], item.get("confidence", "low")) for item in patterns)
    for pid, p_str, confidence, compiled_re in _compile_patterns(patterns_tuple):
        try:
            match = (
                compiled_re.search(text, timeout=_REGEX_TIMEOUT)
                if _REGEX_TIMEOUT else compiled_re.search(text)
            )
            if match:
                return True, "Yêu cầu không được phép.", {"id": pid, "pattern": p_str, "confidence": confidence}
        except (TimeoutError, re_lib.error):
            pass
    return False, "", None


def _build_input_context(query: str, messages: list[dict[str, Any]] | None) -> str:
    """Ghép lịch sử hội thoại gần nhất + câu hỏi hiện tại làm ngữ cảnh cho
    LLM check — trước đây chỉ check mỗi query trần, thiếu ngữ cảnh hội thoại."""
    if not messages:
        return query
    recent = messages[-_HISTORY_TURNS:]
    lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in recent]
    lines.append(f"user: {query}")
    return "\n".join(lines)


def _fail_closed(cfg: dict[str, Any]) -> bool:
    """cfg (từ bảng "Guardrails") thắng env; env là mặc định của deployment."""
    value = cfg.get("guardrail_fail_closed")
    if value is None:
        return _ENV_FAIL_CLOSED
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


async def run_guardrail(query: str, cfg: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> tuple[bool, str]:
    """
    Trả về (triggered, reason).
    cfg có thể truyền vào guardrail_input_patterns, guardrail_llm_enabled, guardrail_model, v.v.
    messages: lịch sử hội thoại gần nhất, dùng làm ngữ cảnh cho lớp LLM check
    (lớp regex chỉ check `query` — mẫu câu jailbreak/injection nhắm vào câu
    hỏi hiện tại, không cần lịch sử).

    Khi lớp LLM hỏng, KHÔNG vứt luôn kết quả regex như trước: regex là lớp
    phòng thủ duy nhất còn lại, và một pattern đã khớp là bằng chứng độc lập
    với việc model check có sống hay không. Chỉ khi cả hai lớp đều không nói
    được gì mới rơi vào lựa chọn fail-open/fail-closed (xem _fail_closed).
    """
    t0 = time.monotonic()

    regex_triggered, regex_message, regex_matched = _regex_check(
        query, cfg.get("guardrail_input_patterns", [])
    )

    if regex_triggered and regex_matched and regex_matched.get("confidence") == "high":
        logger.info("guardrail | latency=%dms triggered=True method=regex-hard", int((time.monotonic() - t0) * 1000))
        return True, regex_message

    triggered, message, method = False, "", "regex"
    if cfg.get("guardrail_llm_enabled", True):
        context = _build_input_context(query, messages)
        try:
            triggered, message = await llm_guardrail_check(context, cfg, check_type="input")
            method = "llm"
        except GuardrailCheckError as e:
            # Lớp LLM im lặng → hạ cấp về verdict của regex ở MỌI mức
            # confidence (bình thường chỉ "high" mới tự chặn, phần còn lại
            # nhường LLM phân xử — giờ không còn ai phân xử nữa).
            if regex_triggered:
                logger.warning(
                    "guardrail | LLM check failed, falling back to regex verdict "
                    "(pattern id=%s confidence=%s). error=%s",
                    (regex_matched or {}).get("id"), (regex_matched or {}).get("confidence"), e,
                )
                triggered, message, method = True, regex_message, "regex-degraded"
            elif _fail_closed(cfg):
                logger.error("guardrail | LLM check failed and no regex verdict → fail-closed. error=%s", e)
                triggered, message, method = True, _DEGRADED_MESSAGE, "fail-closed"
            else:
                logger.warning("guardrail | LLM check failed, no regex verdict → fail-open. error=%s", e)
                triggered, message, method = False, "", "fail-open"
    elif regex_triggered:
        triggered, message = regex_triggered, regex_message

    logger.info(
        "guardrail | latency=%dms triggered=%s method=%s",
        int((time.monotonic() - t0) * 1000), triggered, method,
    )
    return triggered, message
