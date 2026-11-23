"""Every `libs.<package>` a service imports is copied into that service's image.

The service images copy backend/libs package by package (only what the service uses), while the
test suites mount the whole checkout: a missing COPY passes every test and crashes the pod at its
first import. On 21/11/2026 the api imported libs.contracts (AE-06) without copying it, and the
deployed api went into CrashLoopBackOff. This reads each service's imports and its Dockerfile.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SERVICES = ROOT / "backend" / "services"
IMPORT = re.compile(r"^\s*(?:from|import)\s+libs\.(\w+)", re.MULTILINE)
COPY = re.compile(r"^COPY\s+backend/libs/(\w+)/", re.MULTILINE)

pytestmark = [pytest.mark.unit]


def _services() -> list[str]:
    return sorted(p.parent.name for p in SERVICES.glob("*/Dockerfile"))


def _imported(service: str) -> set[str]:
    found: set[str] = set()
    for path in (SERVICES / service).rglob("*.py"):
        found |= set(IMPORT.findall(path.read_text(encoding="utf-8")))
    return found


def _copied(service: str) -> set[str]:
    return set(COPY.findall((SERVICES / service / "Dockerfile").read_text(encoding="utf-8")))


@pytest.mark.parametrize("service", _services())
def test_every_lib_a_service_imports_is_in_its_image(service: str) -> None:
    missing = _imported(service) - _copied(service)
    assert not missing, (
        f"{service} imports libs.{sorted(missing)} but its Dockerfile does not COPY them: "
        "the pod would fail at its first import"
    )
