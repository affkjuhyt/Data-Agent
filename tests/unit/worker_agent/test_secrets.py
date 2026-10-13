"""A source's secret_ref is read only inside its own organisation's subtree."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent.query.secrets import SecretUnavailable, StaticReader

pytestmark = [pytest.mark.unit]

ORG, OTHER = uuid.uuid4(), uuid.uuid4()


@pytest.mark.parametrize(
    "ref",
    [
        f"secret/tenants/{OTHER}/sources/dw",
        f"secret/tenants/{ORG}/../{OTHER}/sources/dw",
        f"secret/tenants/{ORG}/sources/../../{OTHER}/dw",
        f"secret/tenants/{ORG}//sources",
        f"secret/tenants/{ORG}/sources/%2e%2e",
        f"kv/tenants/{ORG}/sources/dw",
    ],
)
async def test_a_ref_outside_the_organisation_is_refused_before_any_call(ref: str) -> None:
    with pytest.raises(SecretUnavailable):
        await StaticReader({ref: {"user": "u", "password": "p"}}).read(ORG, ref)


async def test_its_own_ref_is_read() -> None:
    ref = f"secret/tenants/{ORG}/sources/dw-main"
    creds = await StaticReader({ref: {"username": "u", "password": "p"}}).read(ORG, ref)
    assert (creds.user, creds.password) == ("u", "p")
