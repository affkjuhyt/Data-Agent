"""Whether a candidate can pass ``forecast.model_version_guard`` at publish.

The trigger (BR-158, BR-172) reads ``backtest_run.mase`` and ``in_sample_mase`` as
``numeric(8, 4)``. Publish needs a strictly positive in-sample MASE — the column's
CHECK refuses 0, and a NULL fails the trigger — and refuses a backtest MASE more
than twice that in-sample figure. Selection and the job writer both judge the
rounded values the row will actually store, so a winner cannot end ``ready`` and
then fail when someone accepts the proposal.
"""

from __future__ import annotations

import decimal
import math

# What the person reads when every model that beat seasonal naive would still be
# refused at publish.
UNPUBLISHABLE = (
    "Không mô hình nào đủ điều kiện phát hành: mô hình tốt hơn cách dự báo đơn giản "
    "vẫn thiếu MASE trong mẫu hoặc quá khớp (MASE backtest quá 2 lần MASE trong mẫu), "
    "nên không tạo dự báo."
)

# What the person reads when the fit named a winner the guard would refuse.
CHOSEN_UNPUBLISHABLE = (
    "Mô hình được chọn không đủ điều kiện phát hành (thiếu MASE trong mẫu hoặc quá khớp), "
    "nên không tạo dự báo."
)


def stored_mase(value: float | None) -> decimal.Decimal | None:
    """The ``numeric(8, 4)`` figure, or None when it cannot be stored as a positive MASE."""
    if value is None or not math.isfinite(value):
        return None
    stored = decimal.Decimal(str(round(float(value), 4)))
    if stored <= 0:
        return None
    return stored


def stored_backtest_mase(value: float) -> decimal.Decimal:
    """Backtest MASE as stored. Zero is kept: the guard only refuses a value of 1 or more."""
    return decimal.Decimal(str(round(float(value), 4)))


def passes_publish_guard(mase: float | None, in_sample_mase: float | None) -> bool:
    """True when the stored pair would pass the publish trigger."""
    if mase is None or not math.isfinite(mase):
        return False
    backtest = stored_backtest_mase(mase)
    sample = stored_mase(in_sample_mase)
    if backtest >= 1 or sample is None:
        return False
    return backtest / sample <= 2
