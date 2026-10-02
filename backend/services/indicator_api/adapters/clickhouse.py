"""ClickHouse (`obs` database: observations)."""

from __future__ import annotations

import os

import clickhouse_connect

PING_TIMEOUT_S = 2

# Prefixed, not the shared names: indicator-api writes `obs` as `obs_writer`,
# a user of its own, never as the platform's default ClickHouse user.
URL_ENV = "INDICATOR_API_CLICKHOUSE_URL"
USER_ENV = "INDICATOR_API_CLICKHOUSE_USER"
PASSWORD_ENV = "INDICATOR_API_CLICKHOUSE_PASSWORD"


class ClickHouseAdapter:
    name = "clickhouse"

    def __init__(self, url: str, user: str, password: str) -> None:
        self._url = url
        self._user = user
        self._password = password

    @classmethod
    def from_env(cls) -> ClickHouseAdapter | None:
        url = os.environ.get(URL_ENV, "").strip()
        if not url:
            return None
        return cls(url, os.environ.get(USER_ENV, ""), os.environ.get(PASSWORD_ENV, ""))

    def ping(self) -> None:
        client = clickhouse_connect.get_client(
            dsn=self._url,
            username=self._user or None,
            password=self._password,
            connect_timeout=PING_TIMEOUT_S,
        )
        try:
            if not client.ping():
                raise ConnectionError(f"ClickHouse at {self._url} did not answer ping")
        finally:
            client.close()
