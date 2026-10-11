"""Wire contract for the deterministic verification result shown with an answer."""

from __future__ import annotations

from typing import Literal

from libs.contracts.v1.base import ContractModel, NonEmptyStr


class VerificationCheck(ContractModel):
    name: NonEmptyStr
    passed: bool
    detail: str = ""


class VerificationFrame(ContractModel):
    checks: list[VerificationCheck]
    verdict: Literal["PASS", "FAIL"]
    iteration: int
    confidence: float | None = None
