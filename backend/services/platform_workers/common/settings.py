"""Typed settings for every platform worker (pydantic-settings).

Env names are unchanged from the four services this package replaced, so the
workload env blocks and the Kafka/Postgres credentials keep working: ``KAFKA_*``,
``DB_*``, ``QDRANT_URL`` / ``EMBEDDING_DIM``, ``LITELLM_*``, ``KEYCLOAK_*``.
Worker-specific knobs (topics, group ids, intervals) live in a ``Settings``
class inside each worker package, next to the loop that reads them.

Instances are constructed by the worker's ``run()`` — never at import time —
so a unit test can set env with ``monkeypatch`` and build a fresh object.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaSettings(BaseSettings):
    """``KAFKA_BOOTSTRAP_SERVERS`` and the SASL/PLAIN principal."""

    model_config = SettingsConfigDict(env_prefix="KAFKA_", extra="ignore")

    bootstrap_servers: str = "kafka:9092"
    sasl_username: str = ""
    sasl_password: str = ""
    security_protocol: str = "SASL_PLAINTEXT"
    sasl_mechanism: str = "PLAIN"

    def sasl_kwargs(self) -> dict[str, Any]:
        """kafka-python kwargs for the configured principal — empty when no
        username is set, which is the PLAINTEXT / dev-broker case."""
        if not self.sasl_username:
            return {}
        return {
            "security_protocol": self.security_protocol,
            "sasl_mechanism": self.sasl_mechanism,
            "sasl_plain_username": self.sasl_username,
            "sasl_plain_password": self.sasl_password,
        }


class PostgresSettings(BaseSettings):
    """``DB_HOST`` / ``DB_PORT`` / ``DB_NAME`` / ``DB_USER`` / ``DB_PASSWORD``."""

    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    host: str = "postgres"
    port: int = 8300
    name: str = "dataagent"
    user: str = "admin"
    password: str = ""

    def connect_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
            "password": self.password,
        }


class QdrantSettings(BaseSettings):
    """``QDRANT_URL`` and ``EMBEDDING_DIM`` (1024 = BGE-M3, the platform default)."""

    model_config = SettingsConfigDict(extra="ignore")

    url: str = Field(default="http://qdrant:6333", validation_alias="QDRANT_URL")
    embedding_dim: int = Field(default=1024, validation_alias="EMBEDDING_DIM")


class LiteLLMSettings(BaseSettings):
    """``LITELLM_BASE_URL`` / ``LITELLM_API_KEY``."""

    model_config = SettingsConfigDict(env_prefix="LITELLM_", extra="ignore")

    base_url: str = "http://litellm:4000"
    api_key: str = "sk-dev"


class KeycloakSettings(BaseSettings):
    """``KEYCLOAK_URL`` / ``KEYCLOAK_REALM`` / ``KEYCLOAK_ADMIN`` / ``KEYCLOAK_ADMIN_PASSWORD``.

    ``admin`` / ``admin_password`` authenticate against the ``master`` realm;
    ``realm`` is the realm whose events are read.
    """

    model_config = SettingsConfigDict(env_prefix="KEYCLOAK_", extra="ignore")

    url: str = "http://keycloak-lb:8080"
    realm: str = "aeroflow"
    admin: str = "admin"
    admin_password: str = "admin"
