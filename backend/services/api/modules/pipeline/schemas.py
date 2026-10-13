"""Wire shapes of the pipeline module (tab 4 A3, group "Biến đổi dữ liệu").

Request bodies are A3's fields; responses are the table's columns minus org_id, created_by,
updated_by and deleted_at (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scope = Literal["personal", "data_space"]
OnFailure = Literal["notify", "retry_then_notify", "skip"]
StepKind = Literal["filter", "dedupe", "join", "compute", "aggregate", "cast", "quality_rule"]

# UC-06-01 step 3: a preview runs on a sample of at most 10,000 rows.
MAX_SAMPLE_ROWS = 10_000


class _Body(BaseModel):
    # A field A3 does not name is a client bug, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Step(BaseModel):
    """One transformation step (UC-06-01 step 2): kind, a description in words and its code."""

    model_config = ConfigDict(extra="allow")

    kind: StepKind
    description: str = Field(min_length=1, max_length=2_000)
    language: Literal["sql", "python"] = "sql"
    code: str = Field(min_length=1, max_length=100_000)
    # BR-23: a step that drops rows names the condition it drops them on.
    drop_condition: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _filters_say_what_they_drop(self) -> Step:
        if self.kind in ("filter", "dedupe") and not (self.drop_condition or "").strip():
            raise ValueError(f"a {self.kind} step drops rows and needs drop_condition (BR-23)")
        return self


class PipelineCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    input_dataset_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    steps: list[Step] = Field(default_factory=list, max_length=200)
    scope: Scope = "personal"
    # Words for the data-prep agent to propose steps from (UC-06-01 step 2).
    instruction: str | None = Field(default=None, max_length=4_000)


class PipelinePatch(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    steps: list[Step] | None = Field(default=None, max_length=200)
    scope: Scope | None = None
    status: Literal["draft", "active", "paused"] | None = None
    on_failure: OnFailure | None = None
    depends_on_pipeline_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class PreviewRequest(_Body):
    sample_rows: int = Field(default=MAX_SAMPLE_ROWS, ge=1, le=MAX_SAMPLE_ROWS)


class PipelineMessage(_Body):
    text: str = Field(min_length=1, max_length=4_000)


class MaterializeRequest(_Body):
    # The table lands in the Data Space's work schema ws_{data_space}; a plain identifier.
    table_name: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    scope: Scope | None = None


class ScheduleRequest(_Body):
    """PUT replaces the whole schedule: an omitted field goes back to its default."""

    cron: str | None = Field(default=None, max_length=200)
    work_calendar_id: uuid.UUID | None = None
    trigger_on_job_id: uuid.UUID | None = None
    depends_on_pipeline_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    no_overlap: bool = True
    on_failure: OnFailure = "notify"


class SchedulePreview(_Body):
    cron: str | None = Field(default=None, max_length=200)
    no_overlap: bool = True
    on_failure: OnFailure = "notify"


class StepPreview(BaseModel):
    seq: int
    key_column: str
    column: str
    rows: list[dict[str, str]] = Field(default_factory=list)


class OutputDraft(BaseModel):
    table_name: str
    data_space_id: uuid.UUID
    write_mode: Literal["replace", "append", "merge"] = "replace"
    columns: list[dict[str, Any]] = Field(default_factory=list)
    consumers_text: str | None = None
    input_jobs: list[dict[str, str]] = Field(default_factory=list)
    notify_options: list[dict[str, str]] = Field(default_factory=list)


class PipelineView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    input_dataset_ids: list[uuid.UUID]
    steps: Any
    target_dataset_id: uuid.UUID | None
    scope: str
    status: str
    cron: str | None
    work_calendar_id: uuid.UUID | None
    trigger_on_job_id: uuid.UUID | None
    depends_on_pipeline_ids: list[uuid.UUID]
    no_overlap: bool
    on_failure: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PipelineRunView(_View):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    mode: str
    sample_rows: int | None
    rows_in: int | None
    rows_out: int | None
    rows_dropped: Any
    totals_check: Any
    status: str
    workflow_id: str | None
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    error: str | None
    created_at: datetime.datetime


class PipelineDetail(PipelineView):
    """GET /v1/pipelines/{id}: the pipeline with its latest run (UC-06-02, UC-06-03)."""

    last_run: PipelineRunView | None = None


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 1_000


class LineageNode(BaseModel):
    # A node outside the caller's rights keeps only its place in the graph (UC-06-05 step 2).
    key: str
    kind: str
    id: uuid.UUID | None
    name: str | None = None
    hidden: bool = False


class LineageTarget(BaseModel):
    id: uuid.UUID
    label: str


class LineageLink(BaseModel):
    source: str
    target: str
    via: str | None
    via_ref: uuid.UUID | None


class LineageGroup(BaseModel):
    kind: str
    count: int


class LineageGraph(BaseModel):
    root: LineageNode
    direction: str
    depth: int
    nodes: list[LineageNode]
    edges: list[LineageLink]
    # UC-06-05 E1: above 500 nodes the graph is grouped by kind instead.
    grouped: bool = False
    groups: list[LineageGroup] = Field(default_factory=list)


class MartCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    fact_dataset_id: uuid.UUID
    grain: str = Field(min_length=1, max_length=500)
    measures: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)


class MartPatch(_Body):
    """A3: name, dimensions, measures, relations. A list sent replaces the mart's list, so
    accepting an agent-suggested dimension is the current list plus that one (A1.3)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    dimensions: list[dict[str, Any]] | None = None
    measures: list[dict[str, Any]] | None = None
    relations: list[dict[str, Any]] | None = None


class DataMartView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    name: str
    fact_dataset_id: uuid.UUID
    grain: str
    measures: Any
    dimensions: Any
    relations: Any
    checks: Any
    # Dimensions proposed from the fact table's relations (UC-06-06); null until proposed.
    suggested_dimensions: Any = None
    relation_check_passed: bool
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DataProductView(_View):
    """pipeline.data_product: the shared, contracted product of P06 (layer "published")."""

    id: uuid.UUID
    data_space_id: uuid.UUID
    layer: Literal["published"] = "published"
    code: str
    name: str
    version: int
    dataset_id: uuid.UUID | None
    data_mart_id: uuid.UUID | None
    contract: Any
    consumers: list[uuid.UUID]
    status: str
    breaking_notice_at: datetime.datetime | None
    retire_after: datetime.datetime | None
    owner_id: uuid.UUID
    approved_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WriteWindow(BaseModel):
    """tstzrange as [start, end): the window in which the write may run."""

    model_config = ConfigDict(extra="forbid")

    start: datetime.datetime
    end: datetime.datetime

    @model_validator(mode="after")
    def _ordered(self) -> WriteWindow:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("write_window bounds need a time zone (A1.1)")
        if self.end <= self.start:
            raise ValueError("write_window end must be after start")
        return self


class WritebackCreate(_Body):
    source_id: uuid.UUID
    target_table: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
    from_dataset_id: uuid.UUID
    write_window: WriteWindow | None = None


class UndoRequest(_Body):
    reason: str = Field(min_length=1, max_length=4_000)


class WritebackView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    source_id: uuid.UUID
    target_table: str
    from_dataset_id: uuid.UUID
    requested_by: uuid.UUID
    diff_summary: Any
    diff_ref: str | None
    deletes: int
    write_window: WriteWindow | None
    snapshot_ref: str | None
    approved_by: uuid.UUID | None
    proposal_id: uuid.UUID | None
    status: str
    executed_at: datetime.datetime | None
    totals_check: Any
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None
