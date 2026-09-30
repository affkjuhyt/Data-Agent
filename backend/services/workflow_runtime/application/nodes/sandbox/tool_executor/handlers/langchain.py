from typing import Any
import asyncio
import logging
import uuid
from urllib.parse import quote

import litellm

from services.workflow_runtime.application.nodes.sandbox.tool_executor._utils import _get_query

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")


async def _web_search_handler(call: dict[str, Any], _cfg: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    query = _get_query(call)
    if not query:
        return None, {"tool_name": "web_search", "error": "no query provided"}
    try:
        # DuckDuckGoSearchAPIWrapper.results() (khác DuckDuckGoSearchRun) trả về
        # list[{"title", "link", "snippet"}] — mỗi kết quả giữ được URL riêng, cần
        # thiết để citation trỏ đúng trang thay vì 1 nhãn "web_search" chung chung.
        from langchain_community.utilities import DuckDuckGoSearchAPIWrapper  # noqa: PLC0415
        raw_results = await asyncio.to_thread(
            DuckDuckGoSearchAPIWrapper().results, query, max_results=3,
        )
        items = [
            {
                "id":      f"web_{uuid.uuid4().hex[:8]}",
                "content": f"{r.get('title', '')}\n{r.get('snippet', '')}",
                "source":  r.get("title", "") or r.get("link", ""),
                "url":     r.get("link", ""),
            }
            for r in raw_results if r.get("snippet")
        ]
        if not items:
            return None, {"tool_name": "web_search", "error": "no results found"}
        return items, None
    except Exception as e:
        return None, {"tool_name": "web_search", "error": str(e)}


async def _wikipedia_handler(call: dict[str, Any], _cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    query = _get_query(call)
    if not query:
        return None, {"tool_name": "wikipedia", "error": "no query provided"}
    try:
        import httpx  # noqa: PLC0415
        headers = {"User-Agent": "DataAgent/1.0 (internal tool; not affiliated with Wikimedia)"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search for the most relevant article
            search_resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "format": "json", "list": "search",
                        "srsearch": query, "srlimit": 1, "utf8": 1},
                headers=headers,
            )
            search_resp.raise_for_status()
            results = search_resp.json().get("query", {}).get("search", [])
            if not results:
                return None, {"tool_name": "wikipedia", "error": "no results found"}

            # Fetch intro extract of the top article
            title = results[0]["title"]
            extract_resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "format": "json", "titles": title,
                        "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1},
                headers=headers,
            )
            extract_resp.raise_for_status()
            pages = extract_resp.json().get("query", {}).get("pages", {})
            page  = next(iter(pages.values()))
            content = (page.get("extract") or "").strip()[:3000]
            if not content:
                return None, {"tool_name": "wikipedia", "error": "empty article content"}
            return {"id": f"wiki_{uuid.uuid4().hex[:8]}",
                    "content": f"# {title}\n\n{content}",
                    "source": title,
                    "url": f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"}, None
    except Exception as e:
        return None, {"tool_name": "wikipedia", "error": str(e)}


async def _calculator_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    expression = call.get("arguments", {}).get("expression", "") or _get_query(call)
    if not expression:
        return None, {"tool_name": "calculator", "error": "no expression provided"}
    try:
        from langchain.chains import LLMMathChain  # noqa: PLC0415
        from langchain_openai import ChatOpenAI    # noqa: PLC0415
        llm = ChatOpenAI(model=cfg.get("planner_model", "planner"),
                         base_url=f"{_LITELLM_BASE}/v1", api_key=_LITELLM_KEY,
                         temperature=0, max_tokens=128)
        result = await asyncio.to_thread(LLMMathChain.from_llm(llm=llm).run, expression)
        return {"id": f"calc_{uuid.uuid4().hex[:8]}", "content": result, "source": "tool/calculator"}, None
    except Exception:
        try:
            resp = litellm.completion(
                model=cfg.get("planner_model", "planner"), api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
                messages=[{"role": "user", "content": f"Tính toán và chỉ trả về kết quả số: {expression}"}],
                temperature=0, max_tokens=64, timeout=10,
            )
            return {"id": f"calc_{uuid.uuid4().hex[:8]}", "content": resp.choices[0].message.content.strip(),
                    "source": "tool/calculator"}, None
        except Exception as e:
            return None, {"tool_name": "calculator", "error": str(e)}
