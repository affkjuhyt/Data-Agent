"""Pydantic request schemas for sandbox_executor's HTTP API."""

from __future__ import annotations

import base64
import os
from typing import Any

from input_names import validate_input_name
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutePythonRequest(BaseModel):
    code: str
    timeout_s: int = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
    memory_mb: int = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
    cpus: float = float(os.environ.get("SANDBOX_CPUS", "0.5"))
    files: dict[str, str] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def _validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        total = 0
        for name, encoded in value.items():
            validate_input_name(name)
            try:
                total += len(base64.b64decode(encoded, validate=True))
            except Exception as exc:
                raise ValueError(
                    f"input file {name!r} is not valid base64: {exc}"
                ) from exc

        max_input_bytes = int(
            os.environ.get("SANDBOX_MAX_INPUT_BYTES", str(8 * 1024 * 1024))
        )
        if total > max_input_bytes:
            raise ValueError(
                f"input files total {total} bytes, over the {max_input_bytes}-byte limit "
                "(SANDBOX_MAX_INPUT_BYTES)"
            )
        return value


class ExecuteShellRequest(BaseModel):
    command: str
    timeout_s: int = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
    workdir: str | None = None


class SnowflakeConnectionParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    account: str
    user: str
    password: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    role: str | None = None


class PostgresConnectionParams(BaseModel):
    host: str
    port: int = 5432
    user: str
    password: str | None = None
    dbname: str


class ClickHouseConnectionParams(BaseModel):
    host: str
    port: int = 8123
    user: str
    password: str | None = None
    database: str


CONNECTION_PARAMS: dict[str, type[BaseModel]] = {
    "postgres": PostgresConnectionParams,
    "snowflake": SnowflakeConnectionParams,
    "clickhouse": ClickHouseConnectionParams,
}


class ExecuteSQLRequest(BaseModel):
    sql: str
    connection: dict[str, Any]

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: dict[str, Any]) -> dict[str, Any]:
        backend = str(
            value.get("type") or os.environ.get("SANDBOX_SQL_BACKEND", "snowflake")
        ).lower()
        params_model = CONNECTION_PARAMS.get(backend)
        if params_model is None:
            raise ValueError(
                f"unknown connection type {backend!r} "
                f"(expected one of {sorted(CONNECTION_PARAMS)})"
            )

        if "password" in value:
            raise ValueError("connection_ref is required; plaintext password is not accepted")
        connection_ref = str(value.get("connection_ref") or "").strip()
        if not connection_ref:
            raise ValueError("connection_ref is required")

        params = params_model.model_validate(
            {key: item for key, item in value.items() if key not in {"type", "connection_ref"}}
        )
        return {
            "type": backend,
            "connection_ref": connection_ref,
            **params.model_dump(by_alias=True, exclude_none=True),
        }


__all__ = [
    "ClickHouseConnectionParams",
    "CONNECTION_PARAMS",
    "ExecutePythonRequest",
    "ExecuteSQLRequest",
    "ExecuteShellRequest",
    "PostgresConnectionParams",
    "SnowflakeConnectionParams",
]
