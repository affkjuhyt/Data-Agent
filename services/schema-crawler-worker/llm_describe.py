"""LLM-generated Text Description (doc §2.4) — the business-semantic layer
bridging natural-language questions and warehouse structure. Same call
shape as memory-consumer's call_llm_extract(): one chat completion, JSON
response_format, no retry here (the crawl loop's outer per-table try/except
in main.py skips a table on failure and picks it up again next cron tick).
"""
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "describe_prompt.txt")
with open(_PROMPT_PATH, encoding="utf-8") as _f:
    _DESCRIBE_PROMPT = _f.read()

# Column comments come straight from the warehouse's own
# INFORMATION_SCHEMA.COLUMNS.COMMENT — anyone with COMMENT privilege on the
# source table can set that text, so it's untrusted input flowing into an
# LLM prompt (indirect prompt injection). Truncating it and wrapping it in
# an explicit "this is data, not instructions" tag is a mitigation, not a
# guarantee — describe_table()'s output is still only a hint, never
# executed directly.
_MAX_COMMENT_CHARS = 300
_MAX_OUTPUT_ITEMS = 15
_MAX_OUTPUT_STR_CHARS = 300


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _columns_block(columns: list[dict]) -> str:
    lines = []
    for c in columns:
        comment = _truncate(c.get("comment") or "", _MAX_COMMENT_CHARS)
        lines.append(f"  {c['name']}:{c['type']}:<column_comment>{comment}</column_comment>")
    return "\n".join(lines)


def _cap_str_list(values: list, limit_items: int = _MAX_OUTPUT_ITEMS) -> list[str]:
    return [_truncate(str(v), _MAX_OUTPUT_STR_CHARS) for v in values[:limit_items]]


def describe_table(table_name: str, columns: list[dict], row_count: int | None) -> dict:
    """Returns {keywords: [...], categories: {...}, use_cases: [...], join_hints: [...]}."""
    prompt = (
        _DESCRIBE_PROMPT
        .replace("{table_name}", table_name)
        .replace("{row_count}", str(row_count or "unknown"))
        .replace("{columns}", _columns_block(columns))
    )
    r = httpx.post(
        f"{LITELLM_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        json={
            "model": "planner",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("response is not a JSON object")
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_describe | non-JSON-object response for table=%s: %r", table_name, content[:200])
        parsed = {}
    categories = parsed.get("categories") or {}
    return {
        "keywords": _cap_str_list(parsed.get("keywords") or []),
        "categories": {
            _truncate(str(k), _MAX_OUTPUT_STR_CHARS): _truncate(str(v), _MAX_OUTPUT_STR_CHARS)
            for k, v in list(categories.items())[:_MAX_OUTPUT_ITEMS]
        } if isinstance(categories, dict) else {},
        "use_cases": _cap_str_list(parsed.get("use_cases") or []),
        "join_hints": _cap_str_list(parsed.get("join_hints") or []),
    }


def render_text_description(table_name: str, description: dict) -> str:
    """doc §2.4.2 layout — also the string that gets embedded, so it must
    read naturally for both keyword and vector search."""
    lines = [f"Name: {table_name}"]
    if description["keywords"]:
        lines.append("Keywords: " + ", ".join(description["keywords"]))
    if description["categories"]:
        lines.append("Categories:")
        lines.extend(f"  {col} — {desc}" for col, desc in description["categories"].items())
    if description["use_cases"]:
        lines.append("Use Cases:")
        lines.extend(f"  - {uc}" for uc in description["use_cases"])
    if description["join_hints"]:
        lines.append("Join hints: " + " | ".join(description["join_hints"]))
    return "\n".join(lines)
