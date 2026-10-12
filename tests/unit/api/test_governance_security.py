"""Provider secrets stay inside their organisation and a frontier probe reaches only the
public internet (security review of the model-provider routes)."""

from __future__ import annotations

import uuid

import pytest

from services.api.kernel.problems import Problem
from services.api.modules.governance.routes import _public_https, _tenant_secret_ref

pytestmark = pytest.mark.unit

ORG = uuid.UUID("01920000-0000-7000-8000-000000000001")
OTHER = uuid.UUID("01920000-0000-7000-8000-000000000002")


def test_a_secret_ref_names_a_key_of_the_callers_organisation() -> None:
    mine = f"secret/tenants/{ORG}/providers/frontier-a"
    assert _tenant_secret_ref(mine, ORG) == mine
    for ref in (
        f"secret/tenants/{OTHER}/providers/frontier-a",
        f"secret/tenants/{ORG}/../{OTHER}/providers/frontier-a",
        f"secret/tenants/{ORG}/",
        "kv/data-agent/providers/frontier-a",
        f"secret/tenants/{ORG}/a b",
    ):
        with pytest.raises(Problem):
            _tenant_secret_ref(ref, ORG)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.5/v1",
        "https://169.254.169.254/latest",
        "https://[::1]/v1",
        "https://user:pw@example.com/v1",
        "https://localhost/v1",
    ],
)
def test_a_frontier_endpoint_is_a_public_https_host(url: str) -> None:
    assert _public_https(url) is False
