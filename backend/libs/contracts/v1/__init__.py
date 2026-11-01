"""Contract v1: the wire shapes the worker and the screens agree on (GEN-42 left the V3 ones).

The shared business errors (`ErrorCode`, `Refusal`, the `__refusal` frame, R0-7) and the
`__verification` frame (R0-8). Import from here (`from libs.contracts.v1 import Refusal`), never
from a service.
"""

from __future__ import annotations

from libs.contracts.v1.base import ContractModel
from libs.contracts.v1.errors import (
    HTTP_STATUS,
    NEXT_ACTION,
    USER_MESSAGE_VI,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    NextAction,
    Refusal,
)
from libs.contracts.v1.verification import (
    VerificationCheck,
    VerificationConsensus,
    VerificationFrame,
)

__all__ = [
    "HTTP_STATUS",
    "NEXT_ACTION",
    "USER_MESSAGE_VI",
    "ContractModel",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "NextAction",
    "Refusal",
    "VerificationCheck",
    "VerificationConsensus",
    "VerificationFrame",
]
