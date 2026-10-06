"""Query results as ResultSets (libs.contracts.v1.ResultSet, V3 R0-1).

A SQL or pandas result is not a passage. It used to enter state as one more
text chunk and go through RRF and the reranker with the documents: a table
could lose its place to six PDF paragraphs, and one that kept it was cut to the
responder's character budget like prose. Here the handler's output becomes a
ResultSet that skips ranking altogether, its full rows are stored once under
`result_id` (answer_exports), and every consumer — responder, judge,
visualization, the stream — reads the same bounded object.

Numbering: the responder and the judge both number context items through
`context_items`, result sets first. Citation `[n]` in an answer therefore
points at the same item for the model, the verifier and the reader.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from libs.contracts.v1 import (
    ColumnStats,
    GeneratedBy,
    ResultColumn,
    ResultColumnType,
    ResultKind,
    ResultProvenance,
    ResultSet,
    ResultTruncation,
    ResultWarning,
    TrustLabel,
)
from libs.contracts.v1.result_set import DISTINCT_MAX_VALUES, PREVIEW_MAX_ROWS
from libs.shared import audit, freshness
from services.workflow_runtime.application.answer_exports import create_answer_export

logger = logging.getLogger(__name__)

# Rows of each result set written into the prompt. The stats cover every row,
# so a total stays checkable when the rows behind it do not fit.
DEFAULT_CONTEXT_MAX_RESULT_ROWS = 50

_TRUST = {
    GeneratedBy.VERIFIED_QUERY: TrustLabel.VERIFIED,
    GeneratedBy.SEMANTIC_LAYER: TrustLabel.GOVERNED,
    GeneratedBy.LLM: TrustLabel.EXPLORATORY,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def context_max_rows(cfg: Mapping[str, Any]) -> int:
    """Validated prompt-row budget, independent from document characters."""
    try:
        requested = int(cfg.get("context_max_result_rows", DEFAULT_CONTEXT_MAX_RESULT_ROWS))
    except (TypeError, ValueError):
        requested = DEFAULT_CONTEXT_MAX_RESULT_ROWS
    return max(0, min(requested, PREVIEW_MAX_ROWS))


def is_tabular(item: dict[str, Any]) -> bool:
    """A handler result that carries rows: executed SQL, or a pandas frame."""
    md = item.get("metadata") or {}
    return bool(md.get("sql")) or bool(md.get("tabular"))


def _cell_type(value: Any) -> ResultColumnType | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return ResultColumnType.BOOL
    if isinstance(value, int | float | Decimal):
        return ResultColumnType.NUMBER
    if isinstance(value, dict | list):
        return ResultColumnType.JSON
    text = str(value)
    if _DATE_RE.match(text):
        return ResultColumnType.DATE
    if _DATETIME_RE.match(text):
        return ResultColumnType.DATETIME
    return ResultColumnType.TEXT


def _column_type(values: list[Any]) -> ResultColumnType:
    seen = {t for t in map(_cell_type, values) if t is not None}
    if len(seen) == 1:
        return seen.pop()
    if seen == {ResultColumnType.DATE, ResultColumnType.DATETIME}:
        return ResultColumnType.DATETIME
    # Mixed or all-null: text is the only type every value can be shown as.
    return ResultColumnType.TEXT


def _decimal(value: Any) -> Decimal | None:
    # Through str(): a float's repr is the shortest string that round-trips, so
    # 0.1 stays 0.1 instead of becoming its binary expansion.
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _stats(ctype: ResultColumnType, values: list[Any]) -> ColumnStats:
    present = [v for v in values if v is not None]
    nulls = len(values) - len(present)
    if ctype is ResultColumnType.NUMBER:
        numbers = [n for n in map(_decimal, present) if n is not None]
        if not numbers:
            return ColumnStats(nulls=nulls)
        return ColumnStats(
            nulls=nulls, min=min(numbers), max=max(numbers), sum=sum(numbers, Decimal(0))
        )
    if ctype is ResultColumnType.TEXT:
        distinct = list(dict.fromkeys(str(v) for v in present))
        return ColumnStats(
            nulls=nulls, distinct=distinct[:DISTINCT_MAX_VALUES], distinct_count=len(distinct)
        )
    return ColumnStats(nulls=nulls)


def _kind(columns: list[ResultColumn], row_count: int) -> ResultKind:
    if row_count == 1 and len(columns) == 1:
        return ResultKind.SCALAR
    types = {c.type for c in columns}
    if (
        types & {ResultColumnType.DATE, ResultColumnType.DATETIME}
        and ResultColumnType.NUMBER in types
    ):
        return ResultKind.SERIES
    return ResultKind.TABLE


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _generated_by(value: Any) -> GeneratedBy:
    try:
        return GeneratedBy(value)
    except ValueError:
        return GeneratedBy.LLM


def _warnings(
    truncation: ResultTruncation | None,
    governance: dict[str, Any] | None,
    aggregation: dict[str, Any] | None,
) -> list[ResultWarning]:
    out: list[ResultWarning] = []
    if truncation is not None:
        out.append(ResultWarning(code="truncated", detail=truncation.reason))
    if (governance or {}).get("status") == freshness.STALE:
        out.append(
            ResultWarning(code="stale_data", detail=str((governance or {}).get("as_of") or ""))
        )
    for finding in (aggregation or {}).get("findings") or []:
        if isinstance(finding, dict):
            out.append(
                ResultWarning(
                    code="aggregation_risk",
                    detail=str(finding.get("detail") or finding.get("kind") or ""),
                )
            )
    return out


def build(
    rows: list[dict[str, Any]],
    *,
    connection_id: str,
    engine: str,
    generated_by: str,
    sql: str | None = None,
    truncation: dict[str, Any] | None = None,
    metric: dict[str, Any] | None = None,
    governance: dict[str, Any] | None = None,
    aggregation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A validated ResultSet, dumped to its wire form, for `rows`.

    `full_rows_available` starts False; `capture` sets it once the rows are stored.
    """
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)

    columns_values = {name: [row.get(name) for row in rows] for name in names}
    types = {name: _column_type(values) for name, values in columns_values.items()}
    # The unit is the metric contract's and belongs to its one measure. With
    # several numeric columns there is no saying which one that is.
    numeric = [n for n in names if types[n] is ResultColumnType.NUMBER]
    unit = (metric or {}).get("unit") or None
    columns = [
        ResultColumn(
            name=str(n), type=types[n], unit=unit if len(numeric) == 1 and n in numeric else None
        )
        for n in names
    ]

    truncation_model = (
        ResultTruncation.model_validate(
            {
                k: truncation.get(k)
                for k in (
                    "reason",
                    "rows_returned",
                    "rows_total",
                    "result_bytes",
                    "cells_truncated",
                )
                if k in truncation
            }
        )
        if truncation and truncation.get("reason")
        else None
    )

    origin = _generated_by(generated_by)
    result = ResultSet(
        result_id=uuid.uuid4().hex,
        kind=_kind(columns, len(rows)),
        columns=columns,
        row_count=len(rows),
        rows_preview=rows[:PREVIEW_MAX_ROWS],
        stats={n: _stats(types[n], v) for n, v in columns_values.items()},
        provenance=ResultProvenance(
            connection_id=connection_id or "unknown",
            engine=engine or "unknown",
            # The exact statement is correlated by sql_hash. Only its shape
            # crosses the result-set/storage/SSE boundary: WHERE literals can
            # contain the PII the audit rules are meant to keep out of logs.
            sql=audit.redact_sql(sql) if sql else None,
            sql_hash=audit.statement_hash(sql) if sql else None,
            tables=audit.extract_tables(sql) if sql else [],
            executed_at=datetime.now(UTC),
            watermark=_aware((governance or {}).get("as_of")),
            truncation=truncation_model,
            generated_by=origin,
            metric=metric or None,
            freshness=governance or None,
            aggregation=aggregation or None,
        ),
        trust=_TRUST[origin],
        warnings=_warnings(truncation_model, governance, aggregation),
    )
    return result.model_dump(mode="json")


def _rows_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    content = item.get("content")
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []


def from_handler_item(item: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(ResultSet, every row) for a tabular handler result (`is_tabular`)."""
    md = item.get("metadata") or {}
    rows = _rows_of(item)
    if md.get("sql"):
        rs = build(
            rows,
            connection_id=str(md.get("db_name") or ""),
            engine=str(md.get("db_type") or ""),
            generated_by=md.get("generated_by") or GeneratedBy.LLM,
            sql=md.get("sql"),
            truncation=md.get("truncation"),
            metric=md.get("semantic"),
            governance=md.get("provenance"),
            aggregation=md.get("aggregation"),
        )
    else:
        rs = build(
            rows,
            connection_id=str(md.get("file") or ""),
            engine="pandas",
            generated_by=GeneratedBy.LLM,
            truncation=md.get("truncation"),
        )
    return rs, rows


async def capture(
    items: list[dict[str, Any]],
    *,
    tenant_id: str,
    question: str,
) -> list[dict[str, Any]]:
    """ResultSets for `items`, each with its full rows stored under its result_id.

    Stored here, where the rows exist, rather than by the responder: a canvas
    without a responder, a judge retry or a visualization node all reach the
    same rows by id, and none of them depends on the answer being written.
    A failed store costs the download, never the answer.
    """
    pairs = [from_handler_item(item) for item in items]

    async def _store(rs: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        run = {**evidence_run(rs), "rows": rows}
        stored = await create_answer_export(
            run, tenant_id=tenant_id, question=question, result_id=rs["result_id"]
        )
        rs["full_rows_available"] = stored is not None

    await asyncio.gather(*(_store(rs, rows) for rs, rows in pairs if rows))
    return [rs for rs, _ in pairs]


def context_items(
    state: Mapping[str, Any], cfg: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(result sets, text chunks) the answer is built from, in citation order.

    Every result set, before any chunk: ranking never touched them, so no
    chunk budget may drop one.
    """
    max_chunks = cfg.get("context_max_chunks", 5)
    return list(state.get("result_sets") or []), list(state.get("reranked_chunks") or [])[
        :max_chunks
    ]


def _number(value: Any) -> str:
    return str(value) if value is not None else "-"


def render(rs: dict[str, Any], max_rows: int = DEFAULT_CONTEXT_MAX_RESULT_ROWS) -> str:
    """The result as the model and the judge read it: shape, stats over every
    row, then the first rows as JSON."""
    columns = rs.get("columns") or []
    preview = (rs.get("rows_preview") or [])[: max(0, max_rows)]
    lines = [
        "columns: "
        + ", ".join(
            f"{c['name']} ({c['type']}{', ' + c['unit'] if c.get('unit') else ''})" for c in columns
        ),
        f"rows: {rs.get('row_count', 0)} (showing {len(preview)})",
    ]
    for name, st in (rs.get("stats") or {}).items():
        if st.get("sum") is not None:
            lines.append(
                f"stats {name}: min={_number(st.get('min'))} max={_number(st.get('max'))} "
                f"sum={_number(st.get('sum'))} nulls={st.get('nulls', 0)}"
            )
        elif st.get("distinct_count") is not None:
            lines.append(
                f"stats {name}: {st['distinct_count']} distinct, nulls={st.get('nulls', 0)}"
            )
    lines.append(json.dumps(preview, ensure_ascii=False, default=str))
    return "\n".join(lines)


def export_meta(rs: dict[str, Any]) -> dict[str, Any] | None:
    """What the download buttons need, or None when nothing was stored."""
    if not rs.get("full_rows_available"):
        return None
    result_id = rs["result_id"]
    return {
        "result_id": result_id,
        "preview_rows": len(rs.get("rows_preview") or []),
        "total_rows": rs.get("row_count", 0),
        "full_available": True,
        "xlsx_available": True,
        "file_name": f"ask-result-{result_id[:8]}.xlsx",
        "sheets": ["Dữ liệu", "Nguồn"],
    }


def evidence_run(rs: dict[str, Any], citation_index: int | None = None) -> dict[str, Any]:
    """The pre-R0-1 `__evidence.runs[]` shape, rebuilt from a ResultSet.

    Kept for one release so a client that does not read `__result_set` yet
    still shows the SQL panel; also the source sheet of the Excel export.
    """
    prov = rs.get("provenance") or {}
    preview = rs.get("rows_preview") or []
    run: dict[str, Any] = {
        "id": rs.get("result_id"),
        "kind": "pandas" if prov.get("engine") == "pandas" else "sql",
        "db_name": prov.get("connection_id"),
        "db_type": prov.get("engine"),
        "sql": prov.get("sql") or "",
        "row_count": rs.get("row_count", 0),
        "generated_by": prov.get("generated_by") or GeneratedBy.LLM.value,
        "metric": prov.get("metric"),
        "provenance": prov.get("freshness"),
        "truncation": prov.get("truncation"),
        "aggregation": prov.get("aggregation"),
        "columns": [c["name"] for c in rs.get("columns") or []],
        "rows": preview,
        "rows_capped": rs.get("row_count", 0) > len(preview),
    }
    if citation_index is not None:
        run["citation_index"] = citation_index
    export = export_meta(rs)
    if export:
        run["export"] = export
    return run


def frame(rs: dict[str, Any], citation_index: int | None = None) -> dict[str, Any]:
    """The `__result_set` SSE payload: the ResultSet plus where the answer cites it."""
    return {**rs, "citation_index": citation_index}
