"""Contract v1: the wire shapes the worker and the screens agree on (GEN-42 left the V3 ones).

The shared business errors (`ErrorCode`, `Refusal`, the `__refusal` frame, R0-7) and the
`__verification` frame (R0-8). Import from here (`from libs.contracts.v1 import Refusal`), never
from a service.
"""

from __future__ import annotations

from libs.contracts.v1.analysis_plan import AnalysisOutput, AnalysisPlan, AnalysisStep
from libs.contracts.v1.base import ContractModel
from libs.contracts.v1.errors import (
    HTTP_STATUS,
    NEXT_ACTION,
    USER_MESSAGE_EN,
    USER_MESSAGE_VI,
    USER_MESSAGES,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    NextAction,
    Refusal,
    user_message,
)
from libs.contracts.v1.provider import ProviderRequest
from libs.contracts.v1.result import (
    ResultColumn,
    ResultEnvelope,
    ResultProvenance,
    ResultWarning,
)
from libs.contracts.v1.verification import (
    VerificationCheck,
    VerificationConsensus,
    VerificationFrame,
)

__all__ = [
    "HTTP_STATUS",
    "NEXT_ACTION",
    "USER_MESSAGE_EN",
    "USER_MESSAGE_VI",
    "USER_MESSAGES",
    "AnalysisOutput",
    "AnalysisPlan",
    "AnalysisStep",
    "ContractModel",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "NextAction",
    "ProviderRequest",
    "Refusal",
    "ResultColumn",
    "ResultEnvelope",
    "ResultProvenance",
    "ResultWarning",
    "VerificationCheck",
    "VerificationConsensus",
    "VerificationFrame",
    "user_message",
]
