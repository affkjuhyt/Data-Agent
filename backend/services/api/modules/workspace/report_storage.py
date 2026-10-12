"""Read-only access to report documents stored by the report worker."""

from __future__ import annotations

from typing import Protocol

from services.api.kernel.problems import Problem

REPORT_BUCKET = "da-results"


class ReportStore(Protocol):
    async def download(self, bucket: str, key: str) -> bytes: ...


class UnconfiguredReportStore:
    async def download(self, bucket: str, key: str) -> bytes:
        raise Problem(503, "source_unavailable", "Report storage is not configured.")


_STORE = UnconfiguredReportStore()


def report_store() -> ReportStore:
    return _STORE
