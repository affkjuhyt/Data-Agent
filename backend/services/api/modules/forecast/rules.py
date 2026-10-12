"""Pure rules of the forecast module: no database, no request, so the unit suite covers them.

The DB already holds the life cycles and release gates (routines/forecast.sql); what lives here is
what the API decides before writing: which parameters a formula needs (BR-183), whether a stored
forecast still answers a question (BR-159), the labels a forecast carries (BR-158, BR-160) and the
calibration figures of the ledger (BR-185).
"""

from __future__ import annotations

import datetime
import decimal
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

TERMINAL_JOB = ("ready", "failed", "cancelled")
# What a published model may be asked with: degraded models still answer, labelled (BR-160).
SERVING_VERSION = ("published", "degraded")

# A1.6: how long a client waits before polling again.
POLL_AFTER_MS = 5_000
# UC-15-01 targets: a new job is done in 15 minutes for monthly series, 60 for daily ones.
ETA = {
    "day": datetime.timedelta(minutes=60),
    "week": datetime.timedelta(minutes=30),
    "month": datetime.timedelta(minutes=15),
    "quarter": datetime.timedelta(minutes=15),
}
# Retraining without a backtest to copy the horizon from: one season ahead.
DEFAULT_HORIZON = {"day": 30, "week": 13, "month": 3, "quarter": 2}

# The two inputs a price formula reads besides its parameters (D4.23 formula_model.formula).
FORMULA_INPUTS = frozenset({"world_avg", "fx_rate"})

FUND_NOTE = (
    "Quyết định Quỹ do liên Bộ quyết; mô hình chỉ ước xác suất theo các kỳ đã qua."  # noqa: RUF001
)

# Fewer scored periods than this and the calibration figures are flagged (BR-185, SCR-71).
MIN_CALIBRATION = 10


def formula_parameters(formula: Any) -> set[str]:
    """The parameter codes a formula tree reads: names it uses but does not define.

    A term `{code, op, inputs}` defines `code` from its inputs; a node with a `code` and no
    `inputs` (a leaf) and every string in an `inputs` list is a reference. world_avg and fx_rate
    are the formula's inputs, not parameters.
    """
    defined: set[str] = set()
    used: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            code = node.get("code")
            inputs = node.get("inputs")
            if isinstance(code, str):
                (defined if isinstance(inputs, list) else used).add(code)
            if isinstance(inputs, list):
                used.update(item for item in inputs if isinstance(item, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(formula)
    return used - defined - FORMULA_INPUTS


def expression_names(expression: str) -> set[str]:
    """Identifiers of a certified metric expression: what a formula compiled from it may cite."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))


def forecast_is_current(periods: Iterable[datetime.date], today: datetime.date) -> bool:
    """BR-159: a stored forecast answers while all its periods are still ahead.

    Once the first forecast period has started, its actual may be known and the forecast is no
    longer "after the latest data" (UC-15-01 step 2), so a new job is due.
    """
    first = min(periods, default=None)
    return first is not None and first > today


def labels(version_status: str | None, algorithm: str | None, experimental: bool) -> list[str]:
    """The model label every forecast carries (BR-158, BR-160, UC-15-03 E1)."""
    out: list[str] = []
    if version_status == "published" and not experimental:
        out.append("approved")
    elif version_status == "degraded":
        out.append("degraded")
    else:
        out.append("experimental")
    if algorithm == "seasonal_naive":
        out.append("baseline_model")
    return out


def scenario_notice(assumptions: list[dict[str, Any]], metric_code: str) -> str:
    """BR-173: the conditional-not-causal sentence, filled with the scenario's assumptions."""
    parts = [
        f"{a['feature']} {a['change_kind']}"
        + (f" {a['value']}" if a.get("value") is not None else "")
        + f" từ {a['from_period']}"
        for a in assumptions
    ]
    what = ", ".join(parts)
    features = ", ".join(sorted({str(a["feature"]) for a in assumptions}))
    return (
        "Kịch bản là phép tính có điều kiện, không phải ước lượng tác động nhân quả. "
        f"Nếu {what} và các giả định khác giữ nguyên, mô hình cho kết quả dưới đây. "
        "Mô hình học từ tương quan trong dữ liệu quá khứ, nên chênh lệch này không chứng minh "
        f"{features} gây ra thay đổi của {metric_code}. "
        "Khoảng của kịch bản không gồm độ bất định của giả định."
    )


def feature_names(features: Any) -> set[str]:
    """Names of a version's exogenous variables (model_version.features: [{name, ...}])."""
    if not isinstance(features, list):
        return set()
    return {f["name"] for f in features if isinstance(f, dict) and isinstance(f.get("name"), str)}


@dataclass(frozen=True)
class ScoredPeriod:
    actual: decimal.Decimal
    baseline_value: decimal.Decimal
    abs_error: decimal.Decimal | None
    crps: decimal.Decimal | None
    in80: bool | None
    in95: bool | None
    miss: bool | None


@dataclass(frozen=True)
class CalibrationFigures:
    n_scored: int
    coverage80: decimal.Decimal | None
    coverage95: decimal.Decimal | None
    mae: decimal.Decimal | None
    crps: decimal.Decimal | None
    crps_naive: decimal.Decimal | None
    miss_count: int
    insufficient: bool


def _mean(values: list[decimal.Decimal]) -> decimal.Decimal | None:
    if not values:
        return None
    return (sum(values, decimal.Decimal(0)) / len(values)).quantize(decimal.Decimal("0.0001"))


def calibration(rows: list[ScoredPeriod]) -> CalibrationFigures:
    """Coverage, MAE, CRPS and misses over the last N scored periods (UC-15-08 step 5).

    The naive reference keeps last period's price: a point forecast, whose CRPS is its absolute
    error, so crps_naive needs no stored simulation.
    """
    one, zero = decimal.Decimal(1), decimal.Decimal(0)
    return CalibrationFigures(
        n_scored=len(rows),
        coverage80=_mean([one if r.in80 else zero for r in rows if r.in80 is not None]),
        coverage95=_mean([one if r.in95 else zero for r in rows if r.in95 is not None]),
        mae=_mean([r.abs_error for r in rows if r.abs_error is not None]),
        crps=_mean([r.crps for r in rows if r.crps is not None]),
        crps_naive=_mean([abs(r.actual - r.baseline_value) for r in rows]),
        miss_count=sum(1 for r in rows if r.miss),
        insufficient=len(rows) < MIN_CALIBRATION,
    )


def rate(flags: list[bool]) -> decimal.Decimal | None:
    one, zero = decimal.Decimal(1), decimal.Decimal(0)
    return _mean([one if f else zero for f in flags])
