import pathlib
from typing import Any, cast


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
    """Model output → bare SQL: drop a ``` fence and any trailing semicolons.

    The sandbox runs the statement as `SELECT * FROM (<sql>) … LIMIT n`; a
    trailing ";" inside that subquery is a syntax error, and every retry the
    model made kept the semicolon — three failed attempts on a correct query
    (live E2E). Semicolons are for the terminal, not for a subquery.
    """
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.startswith("```"))
    return text.strip().rstrip(";").rstrip()


def _get_query(call: dict[str, Any]) -> str:
    return cast(str, call.get("query") or call.get("arguments", {}).get("query", ""))


def audit_ctx(cfg: dict[str, Any]) -> dict[str, Any]:
    """Caller identity for an audit record, taken from the verified config.

    Shared by every handler rather than rebuilt per file: an audit trail whose
    records disagree about who the caller was is worse than one that is merely
    incomplete, because the disagreement is invisible until someone reads it.

    A plain dict threaded through the call path rather than a contextvar — the
    handler is the only place that knows the target and the outcome, and a
    record assembled anywhere else would be missing half of what an
    investigation needs.
    """
    return {
        "tenant_id": cfg.get("tenant_id", "") or "",
        "user_id": cfg.get("user_ref", "") or "anonymous",
        "user_roles": list(cfg.get("user_roles") or []),
        "auth_source": cfg.get("auth_source", "") or "",
        "session_id": cfg.get("session_id", "") or "",
        "agent_id": str(cfg.get("agent_id", "") or ""),
    }
