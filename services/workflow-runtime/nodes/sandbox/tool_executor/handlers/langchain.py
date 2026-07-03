import asyncio
import logging
import uuid

import litellm

from nodes.sandbox.tool_executor._utils import _get_query

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")


async def _web_search_handler(call: dict, _cfg: dict) -> tuple[dict | None, dict | None]:
    query = _get_query(call)
    if not query:
        return None, {"tool_name": "web_search", "error": "no query provided"}
    try:
        from langchain_community.tools import DuckDuckGoSearchRun  # noqa: PLC0415
        result = await asyncio.to_thread(DuckDuckGoSearchRun().run, query)
        return {"id": f"web_{uuid.uuid4().hex[:8]}", "content": result, "source": "tool/web_search"}, None
    except Exception as e:
        return None, {"tool_name": "web_search", "error": str(e)}


async def _wikipedia_handler(call: dict, _cfg: dict) -> tuple[dict | None, dict | None]:
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
                    "source": "tool/wikipedia"}, None
    except Exception as e:
        return None, {"tool_name": "wikipedia", "error": str(e)}


async def _calculator_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
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
