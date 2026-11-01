"""Relational provider adapter over the governed query executor."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, cast

from libs.contracts.v1.provider import ProviderRequest
from libs.contracts.v1.result import (
    ResultColumn,
    ResultEnvelope,
    ResultProvenance,
    ResultWarning,
)
from services.worker_agent.query.executor import CallerKind, QueryOutcome, run_query
from services.worker_agent.query.secrets import SecretReader

SourceLoader = Callable[[ProviderRequest], Any | Awaitable[Any]]


class RelationalProvider:
    """Execute a request through the existing scoped relational query path."""

    def __init__(
        self,
        name: str,
        secrets: SecretReader | None,
        *,
        source_loader: SourceLoader | None = None,
    ) -> None:
        self.name = name
        self.secrets = secrets
        self._source_loader = source_loader

    async def execute(self, request: ProviderRequest) -> ResultEnvelope:
        if request.provider != self.name:
            raise ValueError(f"request provider does not match adapter: {request.provider}")
        if self._source_loader is None:
            raise RuntimeError("relational provider has no source loader")
        if self.secrets is None:
            raise RuntimeError("relational provider has no secret reader")
        loaded = self._source_loader(request)
        source = await loaded if inspect.isawaitable(loaded) else loaded
        if source is None:
            raise LookupError(f"source not found: {request.source_id}")
        if (
            source.id != request.source_id
            or source.org_id != request.org_id
            or source.data_space_id != request.data_space_id
        ):
            raise PermissionError("source is outside the provider request scope")

        outcome = await run_query(
            org_id=request.org_id,
            data_space_id=request.data_space_id,
            principal_id=request.principal_id,
            caller_kind=cast(CallerKind, request.caller_kind),
            caller_id=request.caller_id,
            source=source,
            sql=request.sql,
            secrets=self.secrets,
            as_of=request.as_of,
        )
        return _envelope(outcome, source_id=request.source_id)


def _envelope(outcome: QueryOutcome, *, source_id: Any) -> ResultEnvelope:
    columns = [
        ResultColumn(name=str(column["name"]), type=str(column.get("type", "unknown")))
        for column in outcome.columns
    ]
    rows = [
        dict(zip((column.name for column in columns), row, strict=False)) for row in outcome.rows
    ]
    if not outcome.ok:
        code = "timeout" if outcome.status == "timeout" else "failed"
        return ResultEnvelope(
            row_count=0,
            error=outcome.error or "the query failed",
            warnings=[ResultWarning(code=code, message=outcome.error or "the query failed")],
        )
    return ResultEnvelope(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=outcome.truncated,
        provenance=ResultProvenance(
            query_run_id=outcome.query_run_id,
            source_id=source_id,
            trust="governed",
            sql_sha256=hashlib.sha256(outcome.sql.encode()).hexdigest() if outcome.sql else None,
        ),
    )
