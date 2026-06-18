import pathlib


def _safe_path(path: str, allowed_dir: str) -> tuple[str, str | None]:
    """Return (resolved_path, error). Error is None if safe."""
    if not allowed_dir:
        return path, None
    try:
        resolved = str(pathlib.Path(path).resolve())
        allowed  = str(pathlib.Path(allowed_dir).resolve())
        if not resolved.startswith(allowed):
            return "", f"path '{path}' is outside allowed_dir '{allowed_dir}'"
        return resolved, None
    except Exception as e:
        return "", f"invalid path: {e}"


def _strip_code_block(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        return "\n".join(l for l in lines if not l.startswith("```")).strip()
    return text.strip()


def _get_query(call: dict) -> str:
    return call.get("query") or call.get("arguments", {}).get("query", "")
