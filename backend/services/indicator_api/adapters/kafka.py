"""Kafka (the outbox relay publishes indicator-api's events).

Connection check only. Events are written to the outbox and published by the
relay (libs/shared/outbox.py), never sent from a request handler.
"""

from __future__ import annotations

import os

from kafka import KafkaAdminClient

PING_TIMEOUT_MS = 2000

# Prefixed, not the shared KAFKA_* names every pod gets from dataagent-config:
# a Kafka ACL principal is per service, and indicator-api's is declared with
# its topics (docs/v2/11-ops.md K4), not borrowed from another service.
BOOTSTRAP_ENV = "INDICATOR_API_KAFKA_BOOTSTRAP_SERVERS"
SASL_USERNAME_ENV = "INDICATOR_API_KAFKA_SASL_USERNAME"
SASL_PASSWORD_ENV = "INDICATOR_API_KAFKA_SASL_PASSWORD"
SECURITY_PROTOCOL_ENV = "INDICATOR_API_KAFKA_SECURITY_PROTOCOL"
SASL_MECHANISM_ENV = "INDICATOR_API_KAFKA_SASL_MECHANISM"


class KafkaAdapter:
    name = "kafka"

    def __init__(
        self,
        bootstrap_servers: str,
        sasl_username: str = "",
        sasl_password: str = "",
        security_protocol: str = "SASL_PLAINTEXT",
        sasl_mechanism: str = "PLAIN",
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._sasl_username = sasl_username
        self._sasl_password = sasl_password
        self._security_protocol = security_protocol
        self._sasl_mechanism = sasl_mechanism

    @classmethod
    def from_env(cls) -> KafkaAdapter | None:
        bootstrap = os.environ.get(BOOTSTRAP_ENV, "").strip()
        if not bootstrap:
            return None
        return cls(
            bootstrap,
            sasl_username=os.environ.get(SASL_USERNAME_ENV, ""),
            sasl_password=os.environ.get(SASL_PASSWORD_ENV, ""),
            security_protocol=os.environ.get(SECURITY_PROTOCOL_ENV, "SASL_PLAINTEXT"),
            sasl_mechanism=os.environ.get(SASL_MECHANISM_ENV, "PLAIN"),
        )

    def ping(self) -> None:
        kwargs: dict[str, object] = {
            "bootstrap_servers": self._bootstrap_servers,
            "request_timeout_ms": PING_TIMEOUT_MS,
            "api_version_auto_timeout_ms": PING_TIMEOUT_MS,
        }
        if self._sasl_username:
            kwargs.update(
                security_protocol=self._security_protocol,
                sasl_mechanism=self._sasl_mechanism,
                sasl_plain_username=self._sasl_username,
                sasl_plain_password=self._sasl_password,
            )
        # Constructing the client fails when no broker answers; describe_cluster()
        # is one metadata round trip made with the service's own credentials.
        admin = KafkaAdminClient(**kwargs)
        try:
            admin.describe_cluster()
        finally:
            admin.close()
