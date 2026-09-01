"""Environment-backed settings for sandbox_executor."""

from __future__ import annotations

from dataclasses import dataclass
import os


SUPPORTED_SQL_BACKENDS = ("clickhouse", "postgres", "snowflake")


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    timeout_s: int
    memory_mb: int
    cpus: float
    python_queue_workers: int
    sql_queue_workers: int
    job_ttl_s: float
    queue_maxsize: int
    max_input_bytes: int
    sse_heartbeat_s: float
    sql_backend: str


def load_settings() -> SandboxSettings:
    sql_backend = os.environ.get("SANDBOX_SQL_BACKEND", "snowflake").lower()
    if sql_backend not in SUPPORTED_SQL_BACKENDS:
        raise ValueError(
            f"unknown SANDBOX_SQL_BACKEND: {sql_backend!r} "
            f"(expected one of {list(SUPPORTED_SQL_BACKENDS)})"
        )

    return SandboxSettings(
        timeout_s=int(os.environ.get("SANDBOX_TIMEOUT_S", "10")),
        memory_mb=int(os.environ.get("SANDBOX_MEMORY_MB", "256")),
        cpus=float(os.environ.get("SANDBOX_CPUS", "0.5")),
        python_queue_workers=int(
            os.environ.get(
                "SANDBOX_PYTHON_QUEUE_WORKERS",
                os.environ.get("SANDBOX_POOL_SIZE", "3"),
            )
        ),
        sql_queue_workers=int(os.environ.get("SANDBOX_SQL_QUEUE_WORKERS", "5")),
        job_ttl_s=float(os.environ.get("SANDBOX_JOB_TTL_S", "600")),
        queue_maxsize=int(os.environ.get("SANDBOX_QUEUE_MAXSIZE", "200")),
        max_input_bytes=int(
            os.environ.get("SANDBOX_MAX_INPUT_BYTES", str(8 * 1024 * 1024))
        ),
        sse_heartbeat_s=float(os.environ.get("SANDBOX_SSE_HEARTBEAT_S", "15")),
        sql_backend=sql_backend,
    )

