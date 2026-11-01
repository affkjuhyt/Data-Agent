"""The provider registry is explicit, typed, and deterministic."""

import pytest

from libs.contracts.v1.provider import ProviderRequest
from libs.contracts.v1.result import ResultEnvelope
from services.worker_agent.providers.base import DataProvider
from services.worker_agent.providers.registry import ProviderNotFound, ProviderRegistry

pytestmark = [pytest.mark.unit]


class FakeProvider:
    name = "fake"

    async def execute(self, request: ProviderRequest) -> ResultEnvelope:
        del request
        return ResultEnvelope()


class NamelessProvider:
    name = ""

    async def execute(self, request: ProviderRequest) -> ResultEnvelope:
        del request
        return ResultEnvelope()


def test_registry_registers_and_lists_providers_in_registration_order() -> None:
    provider = FakeProvider()
    assert isinstance(provider, DataProvider)
    registry = ProviderRegistry()

    registry.register(provider)

    assert registry.get("fake") is provider
    assert registry.names() == ("fake",)


def test_registry_rejects_empty_and_duplicate_names() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ValueError, match="provider name must not be empty"):
        registry.register(NamelessProvider())
    registry.register(FakeProvider())
    with pytest.raises(ValueError, match="provider already registered: fake"):
        registry.register(FakeProvider())


def test_registry_raises_typed_error_for_unknown_provider() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderNotFound, match="unknown provider: relational") as exc_info:
        registry.get("relational")

    assert exc_info.value.name == "relational"


def test_default_registry_contains_the_relational_provider() -> None:
    assert ProviderRegistry.build_default().names() == ("relational",)
    registry = ProviderRegistry.build_default(secrets=object(), source_loader=lambda _request: None)

    assert registry.names() == ("relational",)
