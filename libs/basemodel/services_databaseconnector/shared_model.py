from __future__ import annotations

from typing import TypeVar, Generic
from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 200
    data: T | None = None
    error: str | None = None

class RetryConfig(BaseModel):
    count: int = 5

class HealthCheckLoopConfig(BaseModel):
    count: int = 5
    interval: int = 5
    timeout_for_health_check: int = 5