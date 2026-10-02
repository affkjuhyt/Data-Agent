"""Web crawler and the V2 storage boundary (NOP-163): a source with an
`extraction_profile` still lands its raw file in Bronze, but never auto-promotes
it to Silver — its numbers belong to series_extractor, not to text chunks.
A document source keeps auto-promoting exactly as before.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.platform_workers.workers.web_crawler import worker as wc

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"

PROFILE = {
    "kind": "csv",
    "series": [{"column": "price", "series_key": "EIA:BRENT:D", "unit": "usd_per_barrel"}],
}


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"promotion_mode": "auto", "extraction_profile": PROFILE}, "manual"),
        ({"promotion_mode": "manual", "extraction_profile": PROFILE}, "manual"),
        ({"promotion_mode": "auto"}, "auto"),
        ({"promotion_mode": "auto", "extraction_profile": {}}, "auto"),
        ({}, "manual"),
    ],
)
def test_a_profiled_source_never_auto_promotes(config: dict[str, Any], expected: str) -> None:
    assert wc._promotion_mode("s1", config) == expected


def _session_factory() -> MagicMock:
    """run_source opens a session per step (`with SessionLocal() as session`);
    the run-row bookkeeping it does there is not what these tests are about."""
    session = MagicMock()
    session.__enter__.return_value = session
    return session


def _run(config: dict[str, Any]) -> tuple[MagicMock, MagicMock]:
    source = {
        "source_id": "s1",
        "tenant_id": TENANT,
        "role_id": "r1",
        "connector_type": "http_file",
        "title": "EIA Brent",
        "created_by": "tester",
        "config": {"url": "https://example.invalid/brent.csv", **config},
    }
    with (
        patch.object(wc, "connector_fetch", return_value=MagicMock(extra={})),
        patch.object(wc.bronze, "register_http_file_bronze", return_value="d1") as register,
        patch.object(wc, "queue_auto_promote_silver") as publish,
    ):
        wc.run_source(_session_factory, source, trigger="scheduled", run_id=str(uuid.uuid4()))
    return register, publish


def test_a_profiled_source_lands_in_bronze_but_is_not_queued_for_silver() -> None:
    register, publish = _run({"promotion_mode": "auto", "extraction_profile": PROFILE})

    register.assert_called_once()
    assert register.call_args.kwargs["promotion_mode"] == "manual"
    # source_id is the link kb_backend and the consumers check
    assert register.call_args.kwargs["source_id"] == "s1"
    publish.assert_not_called()


def test_a_document_source_still_auto_promotes() -> None:
    register, publish = _run({"promotion_mode": "auto"})

    assert register.call_args.kwargs["promotion_mode"] == "auto"
    publish.assert_called_once()
    assert publish.call_args.args[1:] == ("d1", TENANT)
