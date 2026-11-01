"""The legacy knowledge base is gone from the gateway and V3 answers in its place (KB-08, 08 §4).

Through Kong on the deployed cluster: the kb-backend routes (/api/data, /api/knowledge,
/api/pipeline, /api/fleet) match nothing any more, and the /v1 resources that replaced them
(documents, collections, document sources, the catalog, crawl sources, conflicts and document
search) answer a signed-in user of the demo Data Space.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.conftest import live_token

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_DATA_SPACE", "DS-DEMO")

RETIRED = (
    "/api/data/health",
    "/api/knowledge/documents",
    "/api/pipeline/light-rag/retrieve",
    "/api/fleet",
)
REPLACEMENTS = (
    "/v1/documents",
    "/v1/collections",
    "/v1/document-sources",
    "/v1/datasets",
    "/v1/sources",
    "/v1/crawl-sources",
    "/v1/conflicts",
)


@pytest.fixture(scope="module")
def headers(live_stack: dict[str, str]) -> dict[str, str]:
    token = live_token(live_stack["keycloak"], "engineer")
    return {"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE}


@pytest.mark.parametrize("path", RETIRED)
def test_a_retired_knowledge_base_route_matches_nothing(
    live_stack: dict[str, str], headers: dict[str, str], path: str
) -> None:
    r = httpx.get(f"{live_stack['kong']}{path}", headers=headers, timeout=10)
    # Kong's own answer when no route matches, not an upstream's 404.
    assert r.status_code == 404, r.text[:200]
    assert "no Route matched" in r.text


@pytest.mark.parametrize("path", REPLACEMENTS)
def test_the_v1_replacement_answers(
    live_stack: dict[str, str], headers: dict[str, str], path: str
) -> None:
    r = httpx.get(f"{live_stack['kong']}{path}", headers=headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    assert isinstance(r.json().get("items"), list)


def test_document_search_answers_in_place_of_light_rag(
    live_stack: dict[str, str], headers: dict[str, str]
) -> None:
    r = httpx.post(
        f"{live_stack['kong']}/v1/search/documents",
        headers=headers,
        json={"query": "chính sách chiết khấu", "top_k": 5},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:300]
    assert isinstance(r.json().get("items"), list)
