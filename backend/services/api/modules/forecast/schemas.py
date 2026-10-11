"""Request and response shapes of the forecast module (tab 4 A3, group "Dự báo").

Responses are the table's columns minus org_id, created_by and updated_by (A3 "Trả về"), plus the
labels and summaries A3 asks for next to them.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.api.kernel.pagination import Page

Frequency = Literal["day", "week", "month", "quarter"]
Unit = Literal["percent", "vnd_per_litre", "vnd_per_kg", "usd_per_bbl", "ratio"]
Issue = Literal["none", "bulletin", "answer"]


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _not_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class Assumption(BaseModel):
    """A future value of an exogenous variable (scenario.assumptions, forecast_job.assumptions)."""

    feature: str = Field(min_length=1, max_length=200)
    from_period: datetime.date
    change_kind: str = Field(min_length=1, max_length=50)
    value: decimal.Decimal | None = None


# ---------------------------------------------------------------- jobs (UC-15-01)


class ForecastCreate(BaseModel):
    metric_code: str = Field(min_length=1, max_length=200)
    frequency: Frequency
    horizon: int = Field(ge=1, le=1000)
    scope: dict[str, Any] = Field(default_factory=dict)
    question_id: uuid.UUID | None = None
    assumptions: list[Assumption] = Field(default_factory=list, max_length=50)
    notify_channels: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("notify_channels")
    @classmethod
    def _channels(cls, value: list[str]) -> list[str]:
        if any(not c.strip() or len(c) > 50 for c in value):
            raise ValueError("each channel is a non-blank name of at most 50 characters")
        return value


class ForecastCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


class PredictionView(_View):
    id: uuid.UUID
    model_version_id: uuid.UUID
    job_id: uuid.UUID | None
    scenario_id: uuid.UUID | None
    cutoff_at: datetime.datetime
    period: datetime.date
    point: decimal.Decimal
    lo80: decimal.Decimal
    hi80: decimal.Decimal
    lo95: decimal.Decimal
    hi95: decimal.Decimal
    interval_method: str
    exog_uncertainty: str
    contributions: Any
    is_experimental: bool
    created_at: datetime.datetime


class ForecastJobView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    question_id: uuid.UUID | None
    requested_by: uuid.UUID
    metric_code: str
    scope: Any
    assumptions: Any
    frequency: str
    horizon: int
    cutoff_at: datetime.datetime
    status: str
    eta_at: datetime.datetime | None
    notify_channels: list[str]
    progress: Any
    model_version_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class ForecastDetail(ForecastJobView):
    # Filled once the job is ready: the base forecast (no scenario), its labels (approved,
    # experimental, degraded, baseline_model) and the date the data was cut at (BR-157).
    predictions: list[PredictionView] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    data_as_of: datetime.datetime | None = None


class Accepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


class ForecastAccepted(Accepted):
    eta_at: datetime.datetime | None = None


# ---------------------------------------------------------------- models (UC-15-04 to 15-06)


class ModelVersionView(_View):
    id: uuid.UUID
    model_id: uuid.UUID
    version: int
    algorithm: str
    ensemble_of: Any
    features: Any
    params: Any
    training_from: datetime.date
    training_to: datetime.date
    data_snapshot_refs: Any
    n_points: int | None
    n_exog: int
    pooled_series: int
    backtest_id: uuid.UUID | None
    status: str
    model_card: Any
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ForecastModelView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    metric_code: str
    scope: Any
    frequency: str
    owner_id: uuid.UUID
    status: str
    current_version_id: uuid.UUID | None
    retrain_cron: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # A3: rolling MASE next to the backtest MASE, and the degradation label (BR-160).
    backtest_mase: decimal.Decimal | None = None
    rolling_mase: decimal.Decimal | None = None
    degraded: bool = False


class ForecastModelDetail(ForecastModelView):
    current_version: ModelVersionView | None = None


class BacktestView(_View):
    id: uuid.UUID
    model_version_id: uuid.UUID
    frequency: str
    cutoffs: list[datetime.date]
    horizon: int
    metrics: Any
    baseline_metrics: Any
    mase: decimal.Decimal
    in_sample_mase: decimal.Decimal | None
    candidates: Any
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class VersionNote(BaseModel):
    note: str | None = Field(default=None, max_length=4_000)


class VersionReject(BaseModel):
    reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class RetrainRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)
    training_to: datetime.date | None = None


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    assumptions: list[Assumption] = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class ScenarioAccepted(Accepted):
    model_id: uuid.UUID
    model_version_id: uuid.UUID
    name: str
    assumptions: Any
    # BR-173: the sentence every place showing a scenario carries, filled with the assumption.
    notice: str


class PredictionPage(Page[PredictionView]):
    model_version_id: uuid.UUID | None = None
    labels: list[str] = Field(default_factory=list)
    assumptions: Any = None
    notice: str | None = None


class AccuracyView(_View):
    id: uuid.UUID
    model_version_id: uuid.UUID
    period: datetime.date
    actual: decimal.Decimal
    point: decimal.Decimal
    abs_scaled_error: decimal.Decimal
    in80: bool
    in95: bool
    rolling_mase: decimal.Decimal | None
    recorded_at: datetime.datetime


class AccuracyPage(Page[AccuracyView]):
    rolling_mase: decimal.Decimal | None = None
    backtest_mase: decimal.Decimal | None = None
    in80_rate: decimal.Decimal | None = None
    in95_rate: decimal.Decimal | None = None
    degraded: bool = False


# ---------------------------------------------------------------- formula models (UC-15-07)


class FormulaModelCreate(BaseModel):
    model_id: uuid.UUID
    metric_version_id: uuid.UUID
    product_code: str = Field(pattern=r"^[a-z0-9_]+$", max_length=50)
    formula: dict[str, Any]
    window_rule: dict[str, Any]
    input_series: dict[str, Any]
    nowcast_method: Literal["bridge_regression", "published_only"] = "bridge_regression"
    n_draws: int = Field(default=10_000, ge=10_000, le=10_000_000)
    miss_abs_threshold: decimal.Decimal = Field(default=decimal.Decimal(300), gt=0)
    rounding: Literal["integer", "ten"] = "integer"


class FormulaModelView(_View):
    id: uuid.UUID
    model_id: uuid.UUID
    model_version_id: uuid.UUID
    version_status: str
    metric_version_id: uuid.UUID
    product_code: str
    formula: Any
    window_rule: Any
    input_series: Any
    nowcast_method: str
    fund_model: Any
    n_draws: int
    miss_abs_threshold: decimal.Decimal
    rounding: str
    owner_id: uuid.UUID
    reconstruction_mae: decimal.Decimal | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ComponentCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    # '*' applies to every product (vat_rate).
    product_code: str = Field(pattern=r"^([a-z0-9_]+|\*)$", max_length=50)
    value: decimal.Decimal
    unit: Unit
    valid_from: datetime.datetime
    valid_to: datetime.datetime | None = None
    # BR-182: the value comes from a legal document, cited by number and link.
    legal_doc_number: str = Field(min_length=1, max_length=200)
    legal_doc_url: str = Field(pattern=r"^https?://\S+$", max_length=2_000)
    document_id: uuid.UUID | None = None
    extracted_record_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=4_000)

    @field_validator("legal_doc_number")
    @classmethod
    def _legal_doc_number_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class ComponentView(_View):
    id: uuid.UUID
    code: str
    product_code: str
    value: decimal.Decimal
    unit: str
    valid_from: datetime.datetime
    valid_to: datetime.datetime | None
    legal_doc_number: str
    legal_doc_url: str
    document_id: uuid.UUID | None
    extracted_record_id: uuid.UUID | None
    status: str
    # Who entered the value: SCR-64 shows them, and BR-182 forbids them to certify it.
    entered_by: uuid.UUID | None
    certified_by: uuid.UUID | None
    certified_at: datetime.datetime | None
    hitl_request_id: uuid.UUID | None
    note: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ComponentCertify(BaseModel):
    note: str | None = Field(default=None, max_length=4_000)


class FormulaRunCreate(BaseModel):
    period: datetime.date
    product_codes: list[str] | None = Field(default=None, min_length=1, max_length=20)
    event_id: uuid.UUID | None = None
    issue: Issue = "none"


class DistributionItem(BaseModel):
    product_code: str
    model_version_id: uuid.UUID
    period: datetime.date
    point: decimal.Decimal
    lo80: decimal.Decimal
    hi80: decimal.Decimal
    lo95: decimal.Decimal
    hi95: decimal.Decimal
    quantiles: Any
    prob_up: decimal.Decimal | None
    fund_probs: Any
    contributions: Any
    histogram: Any
    baseline_value: decimal.Decimal | None
    is_experimental: bool
    ledger_entry_id: uuid.UUID | None


class DistributionView(BaseModel):
    job_id: uuid.UUID
    cutoff_at: datetime.datetime
    items: list[DistributionItem]
    # BR-184: the Fund part always carries this sentence.
    fund_note: str


# ---------------------------------------------------------------- ledger (UC-15-08)


class LedgerEntryView(_View):
    id: uuid.UUID
    forecast_model_id: uuid.UUID
    model_version_id: uuid.UUID
    prediction_id: uuid.UUID
    job_id: uuid.UUID | None
    period: datetime.date
    scope: Any
    issued_at: datetime.datetime
    issued_via: str
    event_id: uuid.UUID | None
    event_at: datetime.datetime | None
    baseline_value: decimal.Decimal
    point: decimal.Decimal
    quantiles: Any
    prob_up: decimal.Decimal | None
    fund_probs: Any
    contributions: Any
    status: str
    actual: decimal.Decimal | None
    actual_source_ref: Any
    actual_components: Any
    abs_error: decimal.Decimal | None
    crps: decimal.Decimal | None
    pit: decimal.Decimal | None
    in80: bool | None
    in95: bool | None
    miss: bool | None
    miss_attribution: Any
    error_book_entry_id: uuid.UUID | None
    accuracy_log_id: uuid.UUID | None
    scored_at: datetime.datetime | None
    void_reason: str | None
    created_at: datetime.datetime


class Calibration(BaseModel):
    forecast_model_id: uuid.UUID
    product_code: str | None
    last_n: int
    n_scored: int
    coverage80: decimal.Decimal | None
    coverage95: decimal.Decimal | None
    mae: decimal.Decimal | None
    crps: decimal.Decimal | None
    crps_naive: decimal.Decimal | None
    miss_count: int
    # BR-185: fewer than 10 scored periods say little; the screens say so.
    insufficient: bool


class LedgerPage(Page[LedgerEntryView]):
    calibration: Calibration | None = None


class LedgerScore(BaseModel):
    reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        return _not_blank(value)
