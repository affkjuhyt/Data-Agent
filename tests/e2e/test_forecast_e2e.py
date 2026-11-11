"""Train the DS-AW company revenue model, accept it, and read a year of predictions.

Needs the deployed cluster (`LIVE_STACK=1`, `make e2e`) with seed_demo, a certified
DOANH_THU, and ml-forecast reachable from worker-agent. DS-AW allows self-approval, so
helios-engineer accepts the version they just trained.

The company model is the seeded definition when it is there (DOANH_THU, monthly, no
scope). Otherwise the test defines it through the API at horizon 12. Either way one
job is trained and its proposal is accepted.

Not in the default `make e2e` file list (that list is the question smoke). Run it with
`E2E_TESTS=tests/e2e/test_forecast_e2e.py make e2e`. It writes a model version.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from tests.conftest import live_token

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_DATA_SPACE", "DS-AW")
METRIC = "DOANH_THU"
HORIZON = 12
# A monthly fit with a backtest is the product's 15-minute target (rules.ETA).
TRAIN_TIMEOUT = float(os.environ.get("E2E_FORECAST_TIMEOUT", "900"))
PROPOSAL_TIMEOUT = float(os.environ.get("E2E_FORECAST_PROPOSAL_TIMEOUT", "90"))


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE}


def _ordered(row: dict[str, Any]) -> bool:
    lo95, lo80, point, hi80, hi95 = (
        Decimal(str(row[name])) for name in ("lo95", "lo80", "point", "hi80", "hi95")
    )
    return lo95 <= lo80 <= point <= hi80 <= hi95


@pytest.fixture(scope="module")
def token(live_stack: dict[str, str]) -> str:
    return live_token(live_stack["keycloak"], "engineer")


@pytest.fixture(scope="module")
def kong(live_stack: dict[str, str]) -> str:
    return live_stack["kong"]


def _company_model(kong: str, headers: dict[str, str]) -> dict[str, Any] | None:
    response = httpx.get(
        f"{kong}/v1/forecast-models",
        headers=headers,
        params={"metric_code": METRIC, "frequency": "month", "page_size": 50},
        timeout=30,
    )
    assert response.status_code == 200, response.text[:500]
    for item in response.json()["items"]:
        if not item.get("scope"):
            return item
    return None


def _define(kong: str, headers: dict[str, str]) -> tuple[str, str]:
    response = httpx.post(
        f"{kong}/v1/forecast-models",
        headers=headers,
        json={
            "metric_code": METRIC,
            "scope": {},
            "frequency": "month",
            "horizon": HORIZON,
        },
        timeout=30,
    )
    assert response.status_code == 201, response.text[:500]
    body = response.json()
    return body["id"], body["job_id"]


def _retrain(kong: str, headers: dict[str, str], model_id: str) -> str:
    response = httpx.post(
        f"{kong}/v1/forecast-models/{model_id}:retrain",
        headers=headers,
        timeout=30,
    )
    assert response.status_code == 202, response.text[:500]
    return response.json()["id"]


def _wait_ready(kong: str, headers: dict[str, str], job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TRAIN_TIMEOUT
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = httpx.get(f"{kong}/v1/forecasts/{job_id}", headers=headers, timeout=30)
        assert response.status_code == 200, response.text[:500]
        last = response.json()
        if last["status"] == "ready":
            return last
        if last["status"] in {"failed", "cancelled"}:
            error = (last.get("progress") or {}).get("error")
            pytest.fail(f"forecast job {job_id} is {last['status']}: {error}")
        time.sleep(5)
    pytest.fail(
        f"forecast job {job_id} did not finish: {last.get('status')} {last.get('progress')}"
    )


def _accept(kong: str, headers: dict[str, str], version_id: str) -> None:
    """The metric owner's proposal for this version, accepted by helios-engineer."""
    deadline = time.monotonic() + PROPOSAL_TIMEOUT
    while time.monotonic() < deadline:
        inbox = httpx.get(
            f"{kong}/v1/inbox",
            headers=headers,
            params={"kind": "approve_proposal", "page_size": 50},
            timeout=30,
        )
        assert inbox.status_code == 200, inbox.text[:500]
        for item in inbox.json()["items"]:
            if (item.get("context") or {}).get("proposal_kind") != "forecast_model":
                continue
            proposal_id = item["source_ref"]
            proposal = httpx.get(f"{kong}/v1/proposals/{proposal_id}", headers=headers, timeout=30)
            assert proposal.status_code == 200, proposal.text[:500]
            if proposal.json().get("target_ref") != version_id:
                continue
            decided = httpx.post(
                f"{kong}/v1/proposals/{proposal_id}:decide",
                headers=headers,
                json={"decision": "accept"},
                timeout=60,
            )
            assert decided.status_code == 200, decided.text[:500]
            assert decided.json()["status"] == "accepted"
            return
        time.sleep(2)
    pytest.fail(f"no forecast_model proposal for version {version_id}")


def test_the_company_forecast_trains_is_approved_and_reads_twelve_months(
    kong: str, token: str
) -> None:
    headers = _headers(token)
    existing = _company_model(kong, headers)
    if existing is None:
        model_id, job_id = _define(kong, headers)
    else:
        model_id = existing["id"]
        job_id = _retrain(kong, headers, model_id)

    queued = httpx.get(f"{kong}/v1/forecasts/{job_id}", headers=headers, timeout=30)
    assert queued.status_code == 200, queued.text[:500]
    assert queued.json()["horizon"] == HORIZON

    ready = _wait_ready(kong, headers, job_id)
    version_id = ready["model_version_id"]
    assert version_id
    _accept(kong, headers, version_id)

    response = httpx.get(
        f"{kong}/v1/forecast-models/{model_id}/predictions",
        headers=headers,
        params={"page_size": 50},
        timeout=30,
    )
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    items = body["items"]
    assert len(items) == HORIZON
    assert body["model_version_id"] == version_id
    periods = [item["period"] for item in items]
    assert periods == sorted(periods)
    assert all(_ordered(item) for item in items)
    assert all(item["is_experimental"] is False for item in items)
