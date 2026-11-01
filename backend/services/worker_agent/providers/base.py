"""The protocol implemented by every data provider."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from libs.contracts.v1.provider import ProviderRequest
from libs.contracts.v1.result import ResultEnvelope


@runtime_checkable
class DataProvider(Protocol):
    """A provider that executes one already-scoped data request."""

    name: str

    async def execute(self, request: ProviderRequest) -> ResultEnvelope:
        """Execute a request and return a stable result envelope."""
