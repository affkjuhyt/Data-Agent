"""Crawl scope rules the api checks before a source is saved (BR-149)."""

from __future__ import annotations

import uuid

import pytest

from services.api.kernel.problems import Problem
from services.api.modules.acquire import rules

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    "domain",
    [
        "minio.dataagent-database.svc.cluster.local",
        "openbao.internal",
        "printer.local",
        "x.localhost",
    ],
)
def test_internal_names_are_not_crawl_domains(domain: str) -> None:
    with pytest.raises(ValueError, match="internal"):
        rules.normalise_domain(domain)


def test_a_public_domain_is_normalised() -> None:
    assert rules.normalise_domain(" Bank.Example.VN. ") == "bank.example.vn"


def test_a_source_key_lives_under_crawl_sources_of_its_organisation() -> None:
    org = uuid.uuid4()
    assert rules.check_secret_ref(f"tenants/{org}/crawl-sources/bank-a", org)
    for ref in (f"tenants/{org}/sources/dw", f"tenants/{uuid.uuid4()}/crawl-sources/k"):
        with pytest.raises(Problem):
            rules.check_secret_ref(ref, org)
