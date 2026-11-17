"""PL-05: the api counts and times every request by route template and serves /metrics, outside
/v1 (Kong routes /v1 only), without a token."""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from libs import metrics

from .conftest import bearer

pytestmark = [pytest.mark.unit]


def _sample(text: str, name: str, **labels: str) -> float:
    """The value of one series in the exposition text, 0 when it is absent."""
    want = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    for line in text.splitlines():
        m = re.match(rf"^{name}\{{(.*)\}} (\S+)$", line)
        if m and ",".join(sorted(m.group(1).split(","))) == want:
            return float(m.group(2))
    return 0.0


def _scrape(client: TestClient) -> str:
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    return r.text


def test_metrics_are_served_without_a_token_and_outside_the_contract(client: TestClient) -> None:
    text = _scrape(client)
    assert "dataagent_http_requests_total" in text
    assert "/metrics" not in client.get("/v1/openapi.json").json()["paths"]


def test_a_request_is_counted_by_its_route_template_not_its_path(client: TestClient) -> None:
    labels = {
        "service": "api",
        "method": "GET",
        "route": "/v1/receipt-replays/{id}",
        "status": "5xx",
    }
    before = _sample(_scrape(client), "dataagent_http_requests_total", **labels)
    for rid in ("a", "b"):
        assert client.get(f"/v1/receipt-replays/{rid}", headers=bearer("asker")).status_code == 500
    text = _scrape(client)
    assert _sample(text, "dataagent_http_requests_total", **labels) == before + 2
    # The ids never become labels.
    assert 'route="/v1/receipt-replays/a"' not in text


def test_an_unknown_path_is_one_series(client: TestClient) -> None:
    for path in ("/v1/nothing-1", "/v1/nothing-2"):
        client.get(path)
    text = _scrape(client)
    assert 'route="unmatched"' in text and "nothing-1" not in text


def test_probes_and_scrapes_are_not_counted(client: TestClient) -> None:
    client.get("/healthz")
    client.get("/metrics")
    text = _scrape(client)
    assert 'route="/healthz"' not in text and 'route="/metrics"' not in text


def test_an_event_is_counted_ok_or_error_and_its_error_goes_on() -> None:
    async def fine() -> str:
        return "done"

    async def broken() -> None:
        raise ValueError("handler failed")

    def count(outcome: str) -> float:
        return metrics.EVENTS.labels("test", "probe.event", outcome)._value.get()

    ok, error = count("ok"), count("error")
    assert asyncio.run(metrics.timed_event("test", "probe.event", fine)) == "done"
    with pytest.raises(ValueError, match="handler failed"):
        asyncio.run(metrics.timed_event("test", "probe.event", broken))
    assert (count("ok"), count("error")) == (ok + 1, error + 1)
