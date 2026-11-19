from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Purpose(StrEnum):
    ORIENT = "orient"
    ANSWER = "answer"
    ANALYZE = "analyze"
    EXPLAIN = "explain"
    COMPARE = "compare"
    DOCUMENT = "document"


class NumericIntent(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    OPTIONAL = "optional"


class AskProblem(StrEnum):
    ACCESS_DENIED = "ACCESS_DENIED"
    NO_DATA = "NO_DATA"
    NO_READABLE_ASSET = "NO_READABLE_ASSET"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    MISSING_REQUIRED_SLOT = "MISSING_REQUIRED_SLOT"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class RequestContract:
    purpose: Purpose
    numeric_intent: NumericIntent
    missing_required_slots: tuple[str, ...] = ()

    def needs_metric_choice(self) -> bool:
        return (
            self.numeric_intent is NumericIntent.REQUIRED
            and "metric" in self.missing_required_slots
        )
