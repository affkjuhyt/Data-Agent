"""The indicator-api app starts and answers its probe."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.indicator_api.main import app

pytestmark = pytest.mark.unit


def test_healthz():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
