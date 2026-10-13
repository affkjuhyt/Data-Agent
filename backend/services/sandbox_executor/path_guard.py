"""Path confinement used by sandbox callers and offline policy evaluation."""

from __future__ import annotations

import pathlib


def safe_path(path: str, allowed_dir: str) -> tuple[str, str | None]:
    """Return the resolved path, or a safe error when it escapes ``allowed_dir``."""
    if not allowed_dir:
        return path, None
    try:
        resolved = pathlib.Path(path).resolve()
        allowed = pathlib.Path(allowed_dir).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return "", f"invalid path: {exc}"
    try:
        resolved.relative_to(allowed)
    except ValueError:
        return "", f"path '{path}' is outside allowed_dir '{allowed_dir}'"
    return str(resolved), None
