"""sandbox-executor — the only service in the platform that touches the
Docker socket (doc §1.2 hub-and-spoke isolation: one controlled gate, no
direct access from workflow-runtime). Exposes two internal endpoints that
workflow-runtime's execute_python / execute_sql tool handlers call over
HTTP; never reachable from outside the docker-compose network.
"""
import asyncio
import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from security_visitor import validate_python_code, SecurityError
from docker_runner import run_python_sandboxed, ExecutionTimeoutError, SandboxRunError
from output import truncate_output, DEFAULT_MAX_OUTPUT_CHARS
from snowflake_readonly import execute_readonly_query, SnowflakeQueryError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sandbox-executor] %(levelname)s %(message)s")
logger = logging.getLogger("sandbox-executor")

app = FastAPI(title="sandbox-executor", version="0.1.0")

_DEFAULT_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
_DEFAULT_MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
_DEFAULT_CPUS = float(os.environ.get("SANDBOX_CPUS", "0.5"))


class ExecutePythonRequest(BaseModel):
    code: str
    timeout_s: int = _DEFAULT_TIMEOUT_S
    memory_mb: int = _DEFAULT_MEMORY_MB
    cpus: float = _DEFAULT_CPUS


class SnowflakeConnectionParams(BaseModel):
    # `schema` as a field name shadows BaseModel.schema() (Pydantic v2) —
    # alias keeps the wire/dict key "schema" (what snowflake_readonly.py and
    # workflow-runtime's code_interpreter.py both send/expect) while the
    # Python attribute is schema_name.
    model_config = ConfigDict(populate_by_name=True)

    account: str
    user: str
    password: str
    warehouse: str | None = None
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    role: str | None = None


class ExecuteSQLRequest(BaseModel):
    sql: str
    connection: SnowflakeConnectionParams


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/execute/python")
async def execute_python(req: ExecutePythonRequest):
    try:
        validate_python_code(req.code)
    except SecurityError as e:
        logger.warning("execute_python | rejected by AST validation: %s", e)
        return {"success": False, "error": f"security validation failed: {e}"}
    except SyntaxError as e:
        return {"success": False, "error": f"syntax error: {e}"}

    try:
        result = await asyncio.to_thread(run_python_sandboxed, req.code, req.timeout_s, req.memory_mb, req.cpus)
    except ExecutionTimeoutError as e:
        return {"success": False, "error": str(e), "timed_out": True}
    except SandboxRunError as e:
        logger.error("execute_python | sandbox run failed: %s", e)
        return {"success": False, "error": str(e)}

    stdout, truncated = truncate_output(result["stdout"], DEFAULT_MAX_OUTPUT_CHARS)
    return {
        "success": result["exit_code"] == 0,
        "stdout": stdout,
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "timed_out": False,
        "is_truncated": truncated,
    }


@app.post("/execute/sql")
async def execute_sql(req: ExecuteSQLRequest):
    try:
        result = await execute_readonly_query(req.sql, req.connection.model_dump(by_alias=True))
    except SnowflakeQueryError as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "rows": result["rows"],
        "row_count": result["row_count"],
        "is_truncated": result["is_truncated"],
    }
