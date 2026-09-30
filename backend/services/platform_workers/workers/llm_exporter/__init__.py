"""LLM generation-perf Prometheus exporter — ``run()`` serves /metrics and /health."""
from __future__ import annotations


def run() -> None:
    from services.platform_workers.workers.llm_exporter.exporter import main

    main()
