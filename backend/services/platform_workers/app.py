"""Worker application entrypoint for platform-workers.

Keeps worker selection and dispatch in one importable module so the runtime is
not encoded only inside a top-level script.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

WORKERS: dict[str, str] = {
    "memory": "services.platform_workers.workers.memory",
    "schema-crawler": "services.platform_workers.workers.schema_crawler",
    "audit": "services.platform_workers.workers.audit",
    "web-crawler": "services.platform_workers.workers.web_crawler",
    "llm-exporter": "services.platform_workers.workers.llm_exporter",
}


def worker_name_from_env(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get("WORKER", "").strip()


def resolve_worker_module(name: str) -> str | None:
    return WORKERS.get(name)


def unknown_worker_message(name: str) -> str:
    return f"platform-workers: WORKER={name!r} is not one of {sorted(WORKERS)}"
