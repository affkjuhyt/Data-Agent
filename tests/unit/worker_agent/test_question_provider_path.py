"""The question module exposes one process-local provider registry."""

import pytest

from services.worker_agent import questions
from services.worker_agent.providers.registry import ProviderRegistry

pytestmark = [pytest.mark.unit]


def test_question_path_can_use_the_configured_relational_provider() -> None:
    registry = ProviderRegistry.build_default(secrets=object(), source_loader=lambda _request: None)
    questions.set_provider_registry(registry)

    assert questions.provider_registry().get("relational") is registry.get("relational")
