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


def _columns_block(columns: list[dict]) -> str:
    return "\n".join(f"  {c['name']}:{c['type']}:{c.get('comment') or ''}" for c in columns)


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
    except json.JSONDecodeError:
        logger.warning("llm_describe | non-JSON response for table=%s: %r", table_name, content[:200])
        parsed = {}
    return {
        "keywords": parsed.get("keywords") or [],
        "categories": parsed.get("categories") or {},
        "use_cases": parsed.get("use_cases") or [],
        "join_hints": parsed.get("join_hints") or [],
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
