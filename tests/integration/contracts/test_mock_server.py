"""The Contract v1 mock runs as a real server, the way FE/Data/Agent start it (NOP-151)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from libs.contracts.v1 import EntityRef, ErrorBody, ErrorCode

pytestmark = [pytest.mark.integration, pytest.mark.contract, pytest.mark.req("NOP-151")]

REPO_ROOT = Path(__file__).resolve().parents[3]
DRIFT_CASE = "2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT / "backend"), str(REPO_ROOT)]),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.contract_mock", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                output = proc.stdout.read().decode() if proc.stdout else ""
                pytest.fail(f"mock server exited with {proc.returncode}:\n{output}")
            try:
                httpx.get(f"{url}/capabilities", timeout=1).raise_for_status()
                break
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    pytest.fail("mock server did not answer within 30 s")
                time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_serves_contract_models_over_http(base_url: str) -> None:
    response = httpx.get(f"{base_url}/entities")
    assert response.status_code == 200
    assert [EntityRef.model_validate(e).entity_id for e in response.json()]


def test_answers_version_conflict_over_http(base_url: str) -> None:
    response = httpx.post(
        f"{base_url}/review/cases/{DRIFT_CASE}/approve", json={"expected_version": 2}
    )
    assert response.status_code == 409
    error = ErrorBody.model_validate(response.json()).error
    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.current_version == 3
