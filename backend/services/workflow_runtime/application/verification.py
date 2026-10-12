"""Build the public verification frame from graph state, without model input."""

from __future__ import annotations

from typing import Any

from libs.contracts.v1.verification import VerificationFrame


def build_verification_frame(
    verifier: dict[str, Any] | None,
    *,
    judge_verdict: str | None,
    iteration: int,
) -> dict[str, Any] | None:
    """Return one wire frame or None when no deterministic verifier ran."""
    if not isinstance(verifier, dict):
        return None
    checks = verifier.get("checks")
    if not isinstance(checks, list):
        return None
    frame = VerificationFrame(
        checks=[
            {
                "name": item.get("name", "unknown"),
                "passed": bool(item.get("passed")),
                "detail": item.get("detail", ""),
            }
            for item in checks
            if isinstance(item, dict)
        ],
        verdict="PASS" if judge_verdict == "PASS" else "FAIL",
        iteration=max(0, int(iteration)),
    )
    return frame.model_dump()
