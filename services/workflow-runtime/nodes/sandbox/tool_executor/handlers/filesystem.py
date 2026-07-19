from typing import Any
import asyncio
import logging
import pathlib
import uuid

from nodes.sandbox.tool_executor._utils import _safe_path, _get_query

logger = logging.getLogger(__name__)


async def _file_read_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg    = cfg.get("tool_config", {}).get("file_read", {})
    allowed_dir = tool_cfg.get("allowed_dir", "")
    file_path   = call.get("arguments", {}).get("file_path", "")

    if not file_path:
        return None, {"tool_name": "file_read", "error": "no file_path provided"}

    safe, err = _safe_path(file_path, allowed_dir)
    if err:
        return None, {"tool_name": "file_read", "error": err}

    try:
        with open(safe or file_path, encoding="utf-8") as f:
            content = f.read(50_000)
        return {"id": f"file_{uuid.uuid4().hex[:8]}", "content": content, "source": "tool/file", "metadata": {"file": file_path}}, None
    except Exception as e:
        return None, {"tool_name": "file_read", "error": str(e)}


async def _grep_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    import subprocess

    tool_cfg    = cfg.get("tool_config", {}).get("grep", {})
    allowed_dir = tool_cfg.get("allowed_dir", "")

    pattern      = call.get("arguments", {}).get("pattern", "") or _get_query(call)
    search_path  = call.get("arguments", {}).get("path", allowed_dir or ".")
    glob_pattern = call.get("arguments", {}).get("glob")
    max_results  = int(call.get("arguments", {}).get("max_results", 100))

    if not pattern:
        return None, {"tool_name": "grep", "error": "no pattern provided"}

    safe, err = _safe_path(search_path, allowed_dir)
    if err:
        return None, {"tool_name": "grep", "error": err}
    search_path = safe or search_path

    content: str
    try:
        args = ["rg", "--line-number", "--no-heading", "--color=never", f"--max-count={max_results}"]
        if glob_pattern:
            args += ["--glob", glob_pattern]
        args += ["--", pattern, search_path]
        r = await asyncio.to_thread(subprocess.run, args, capture_output=True, text=True, timeout=10)
        if r.returncode in (0, 1):
            content = r.stdout.strip() or "No matches found."
        else:
            raise FileNotFoundError
    except (FileNotFoundError, Exception):
        import re
        import fnmatch as _fnmatch
        try:
            regex = re.compile(pattern)
        except Exception as e:
            return None, {"tool_name": "grep", "error": f"invalid regex: {e}"}

        matches: list[str] = []
        root = pathlib.Path(search_path)
        files = [root] if root.is_file() else list(root.rglob("*"))
        for fp in files:
            if len(matches) >= max_results:
                break
            if not fp.is_file():
                continue
            if glob_pattern and not _fnmatch.fnmatch(fp.name, glob_pattern):
                continue
            try:
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").split("\n"), 1):
                    if len(matches) >= max_results:
                        break
                    if regex.search(line):
                        matches.append(f"{fp}:{i}:{line}")
            except Exception:
                pass
        content = "\n".join(matches) if matches else "No matches found."

    logger.info("grep | pattern=%r → %d lines", pattern, content.count("\n") + 1)
    return {"id": f"grep_{uuid.uuid4().hex[:8]}", "content": content, "source": "tool/grep",
            "metadata": {"pattern": pattern, "path": search_path}}, None


async def _glob_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg    = cfg.get("tool_config", {}).get("glob", {})
    allowed_dir = tool_cfg.get("allowed_dir", "")

    search_path = call.get("arguments", {}).get("path", allowed_dir or ".")
    pattern     = call.get("arguments", {}).get("pattern", "**/*")
    max_files   = int(call.get("arguments", {}).get("max_files", 500))

    safe, err = _safe_path(search_path, allowed_dir)
    if err:
        return None, {"tool_name": "glob", "error": err}
    search_path = safe or search_path

    _SKIP = {"node_modules", ".git", "__pycache__", "dist", "build", ".venv", "venv"}
    try:
        root  = pathlib.Path(search_path)
        files = [
            f for f in root.rglob(pattern)
            if f.is_file() and not any(p in _SKIP for p in f.parts)
        ][:max_files]
        content = "\n".join(sorted(str(f.relative_to(root)) for f in files)) or "No files matched."
    except Exception as e:
        return None, {"tool_name": "glob", "error": str(e)}

    return {"id": f"glob_{uuid.uuid4().hex[:8]}", "content": content, "source": "tool/glob"}, None


async def _file_write_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg    = cfg.get("tool_config", {}).get("file_write", {})
    allowed_dir = tool_cfg.get("allowed_dir", "")

    file_path = call.get("arguments", {}).get("file_path", "")
    content   = call.get("arguments", {}).get("content", "")

    if not file_path:
        return None, {"tool_name": "file_write", "error": "no file_path provided"}

    safe, err = _safe_path(file_path, allowed_dir)
    if err:
        return None, {"tool_name": "file_write", "error": err}
    file_path = safe or file_path

    try:
        pathlib.Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(file_path).write_text(content, encoding="utf-8")
        logger.info("file_write | path=%s → %d chars", file_path, len(content))
        return {"id": f"write_{uuid.uuid4().hex[:8]}", "content": f"Written {len(content)} chars to {file_path}",
                "source": "tool/file_write"}, None
    except Exception as e:
        return None, {"tool_name": "file_write", "error": str(e)}


async def _file_edit_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg    = cfg.get("tool_config", {}).get("file_edit", {})
    allowed_dir = tool_cfg.get("allowed_dir", "")

    file_path   = call.get("arguments", {}).get("file_path", "")
    old_string  = call.get("arguments", {}).get("old_string", "")
    new_string  = call.get("arguments", {}).get("new_string", "")
    replace_all = bool(call.get("arguments", {}).get("replace_all", False))

    if not file_path or not old_string:
        return None, {"tool_name": "file_edit", "error": "file_path and old_string are required"}

    safe, err = _safe_path(file_path, allowed_dir)
    if err:
        return None, {"tool_name": "file_edit", "error": err}
    file_path = safe or file_path

    try:
        original = pathlib.Path(file_path).read_text(encoding="utf-8")
        count = original.count(old_string)
        if count == 0:
            return None, {"tool_name": "file_edit", "error": "old_string not found in file"}
        if count > 1 and not replace_all:
            return None, {"tool_name": "file_edit", "error": f"old_string appears {count} times; set replace_all=true to replace all"}
        updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
        pathlib.Path(file_path).write_text(updated, encoding="utf-8")
        replaced = count if replace_all else 1
        return {"id": f"edit_{uuid.uuid4().hex[:8]}", "content": f"Replaced {replaced} occurrence(s) in {file_path}",
                "source": "tool/file_edit"}, None
    except Exception as e:
        return None, {"tool_name": "file_edit", "error": str(e)}
