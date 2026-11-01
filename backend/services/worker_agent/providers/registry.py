"""Explicit provider registration and lookup."""

from __future__ import annotations

from collections.abc import Iterable

from services.worker_agent.providers.base import DataProvider


class ProviderNotFound(LookupError):
    """Raised when a requested provider is not registered."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"unknown provider: {name}")


class ProviderRegistry:
    """A process-local registry with deterministic registration order."""

    def __init__(self, providers: Iterable[DataProvider] = ()) -> None:
        self._providers: dict[str, DataProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: DataProvider) -> None:
        name = provider.name.strip()
        if not name:
            raise ValueError("provider name must not be empty")
        if name in self._providers:
            raise ValueError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> DataProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotFound(name) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    @classmethod
    def build_default(cls, *, secrets=None, source_loader=None) -> ProviderRegistry:
        """Build the built-in registry for one worker process."""

        from services.worker_agent.providers.relational import RelationalProvider

        return cls(
            (RelationalProvider(name="relational", secrets=secrets, source_loader=source_loader),)
        )
