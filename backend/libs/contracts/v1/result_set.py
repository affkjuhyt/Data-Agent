"""A query result as a first-class object (V3 R0-1, NOP-279).

A SQL or pandas result used to travel as one more text chunk: its rows were a
JSON string in `content`, ranked by RRF against document passages and cut by
the reranker's top-N and the responder's character budget like any of them.
A table could be dropped for six PDF paragraphs, or reach the model as its
first rows only, and nothing downstream could tell.

`ResultSet` is what the runtime, the stream and the UI agree a result is: its
columns, how many rows it really has, a bounded preview, per-column statistics
computed over every row, where it came from and how far to trust it. The full
rows are stored once under `result_id` (workflow_runtime answer_exports) and
fetched by id; they never ride on the wire.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import AwareDatetime, Field

from libs.contracts.v1.base import ContractModel, DecimalValue, NonEmptyStr, Sha256

PREVIEW_MAX_ROWS = 50
DISTINCT_MAX_VALUES = 20

ResultId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class ResultColumnType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    BOOL = "bool"
    JSON = "json"


class ResultKind(StrEnum):
    """`scalar` is one row of one column, `series` has a date column to plot against."""

    TABLE = "table"
    SCALAR = "scalar"
    SERIES = "series"


class GeneratedBy(StrEnum):
    """Who decided the statement's shape — the first fact behind "why trust this number"."""

    SEMANTIC_LAYER = "semantic_layer"
    VERIFIED_QUERY = "verified_query"
    LLM = "llm"


class TrustLabel(StrEnum):
    """Trust of a computed number (docs/v3/01 §4). DOCUMENT and INSUFFICIENT
    label answer blocks, not results, so they are not values here."""

    VERIFIED = "VERIFIED"
    GOVERNED = "GOVERNED"
    EXPLORATORY = "EXPLORATORY"


class ResultColumn(ContractModel):
    name: NonEmptyStr
    type: ResultColumnType
    unit: str | None = None


class ColumnStats(ContractModel):
    """Computed over every row returned, not the preview: a total the preview
    cannot show is still a number the answer may state, and the judge checks it
    against this. When the source capped the result, `provenance.truncation`
    says so and the stats describe the rows that came back.

    Numeric columns fill min/max/sum; text columns fill `distinct` (first
    DISTINCT_MAX_VALUES, in row order) and `distinct_count`.
    """

    nulls: int = Field(ge=0)
    min: DecimalValue | None = None
    max: DecimalValue | None = None
    sum: DecimalValue | None = None
    distinct: list[str] | None = Field(default=None, max_length=DISTINCT_MAX_VALUES)
    distinct_count: int | None = Field(default=None, ge=0)


class ResultTruncation(ContractModel):
    """The source capped the result: `row_count` is what came back, not what exists."""

    reason: NonEmptyStr
    rows_returned: int = Field(ge=0)
    # Known only when the source counted before capping (a pandas frame); a
    # database cap stops reading, so it cannot say how many rows it skipped.
    rows_total: int | None = Field(default=None, ge=0)
    result_bytes: int | None = Field(default=None, ge=0)
    cells_truncated: int | None = Field(default=None, ge=0)


class ResultWarning(ContractModel):
    code: NonEmptyStr
    detail: str = ""


class ResultProvenance(ContractModel):
    """Where the rows came from. `sql` keeps the executed statement's shape but
    has literals redacted by the audit policy; `sql_hash` identifies the exact
    original without copying sensitive predicate values onto the wire."""

    connection_id: NonEmptyStr
    engine: NonEmptyStr
    sql: str | None = None
    sql_hash: Sha256 | None = None
    tables: list[str] = Field(default_factory=list)
    executed_at: AwareDatetime
    # Newest business date the tables were loaded to, when the catalog measured one.
    watermark: AwareDatetime | None = None
    truncation: ResultTruncation | None = None
    generated_by: GeneratedBy
    # Opaque facts owned by other modules, passed through for the UI and the
    # export sheet: the metric contract (libs.semantic), freshness/owner/
    # classification (libs.shared.freshness) and the fan-out check
    # (libs.shared.aggregation_check).
    metric: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None
    aggregation: dict[str, Any] | None = None


class ResultSet(ContractModel):
    result_id: ResultId
    kind: ResultKind
    columns: list[ResultColumn]
    row_count: int = Field(ge=0)
    rows_preview: list[dict[str, Any]] = Field(max_length=PREVIEW_MAX_ROWS)
    stats: dict[str, ColumnStats] = Field(default_factory=dict)
    provenance: ResultProvenance
    trust: TrustLabel
    warnings: list[ResultWarning] = Field(default_factory=list)
    # False when the full rows could not be stored: the UI hides the download
    # rather than offering a link that 404s.
    full_rows_available: bool = False
