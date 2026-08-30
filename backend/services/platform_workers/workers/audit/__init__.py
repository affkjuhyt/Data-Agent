"""Audit worker — ``run()`` consumes audit.data.access and bridges Keycloak auth events."""
from __future__ import annotations


def run() -> None:
    from services.platform_workers.workers.audit.consumer import run as _run

    _run()
