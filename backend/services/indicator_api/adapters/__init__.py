"""Connections to the stores indicator-api reads and writes.

An adapter exists only when its environment configures it, so the service boots
with none of them and `/ready` reports the missing ones as `not_configured`
rather than failing: indicator-api has no Kafka, ClickHouse or MinIO principal
of its own yet, and each arrives with the issue that first uses that store.
"""

from __future__ import annotations

from typing import Protocol

from services.indicator_api.adapters.clickhouse import ClickHouseAdapter
from services.indicator_api.adapters.kafka import KafkaAdapter
from services.indicator_api.adapters.minio import MinioAdapter
from services.indicator_api.adapters.postgres import PostgresAdapter


class Adapter(Protocol):
    name: str

    def ping(self) -> None:
        """Return when the store answers; raise otherwise."""


# Every adapter /ready reports on, configured or not.
ADAPTER_NAMES: tuple[str, ...] = (
    PostgresAdapter.name,
    ClickHouseAdapter.name,
    KafkaAdapter.name,
    MinioAdapter.name,
)


def configured_adapters() -> list[Adapter]:
    candidates: list[Adapter | None] = [
        PostgresAdapter.from_env(),
        ClickHouseAdapter.from_env(),
        KafkaAdapter.from_env(),
        MinioAdapter.from_env(),
    ]
    return [adapter for adapter in candidates if adapter is not None]
