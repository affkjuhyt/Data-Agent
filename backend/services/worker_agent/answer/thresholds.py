"""A result judged against its metric's threshold watch (monitor.monitor, rule_kind threshold).

The threshold is the one the risk team set on the metric for the whole Data Space (an empty scope
filter): `{"direction": "above" | "below", "value": n, "early": m?}` as the watch screen writes it
(WatchCreatePage). A member is over it ("vượt ngưỡng") past the value, in the early-warning band
from the early level, otherwise within it. The threshold and its early level become figures
F<n+1>… whose source is a constant expression, so the sentence carries no untraced number and the
Narrator gets it as a hint like the insights (answer/insights.py).

Nothing is judged when a figure is masked or missing (BR-178): saying a hidden member is over the
threshold would reveal it.
"""

from __future__ import annotations

import decimal
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import monitor
from services.worker_agent.answer.insights import Insights
from services.worker_agent.answer.narrate import Figure

ROLE = "svc_monitor"
State = Literal["breach", "early", "ok"]


@dataclass(frozen=True)
class Threshold:
    direction: Literal["above", "below"]
    value: decimal.Decimal
    early: decimal.Decimal | None
    text: str | None


def _number(raw: Any) -> decimal.Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None


def threshold_of(rule: Any) -> Threshold | None:
    if not isinstance(rule, dict) or rule.get("direction") not in ("above", "below"):
        return None
    value = _number(rule.get("value"))
    if value is None:
        return None
    early = _number(rule.get("early"))
    # An early level past the threshold would never warn first: it is not one.
    if early is not None and (early >= value if rule["direction"] == "above" else early <= value):
        early = None
    # The watch's own wording names its early level: when that level was dropped, so is the text.
    text = rule.get("text") if early is not None or rule.get("early") is None else None
    return Threshold(rule["direction"], value, early, text if isinstance(text, str) else None)


def state(value: decimal.Decimal, t: Threshold) -> State:
    over = value > t.value if t.direction == "above" else value < t.value
    if over:
        return "breach"
    if t.early is not None and (value >= t.early if t.direction == "above" else value <= t.early):
        return "early"
    return "ok"


def _member(f: Figure) -> str:
    return ", ".join(v for v in f.dimensions.values() if v) or f.label


def judged(figures: list[Figure], t: Threshold, *, metric_name: str, next_key: int) -> Insights:
    """The threshold figures and the sentence saying where each member stands."""
    if not figures or any(f.display_state != "shown" or f.value is None for f in figures):
        return Insights()
    out = Insights()
    base = figures[0]

    def constant(label: str, value: decimal.Decimal) -> str:
        key = f"F{next_key + len(out.figures)}"
        out.figures.append(
            Figure(key, label, value, base.unit, base.decimals, -1, "value", expr=str(value))
        )
        return f"{{{{{key}}}}}"

    limit = constant(f"Ngưỡng theo dõi {metric_name}", t.value)
    early = constant(f"Mức cảnh báo sớm {metric_name}", t.early) if t.early is not None else None
    states = [(f, state(f.value, t)) for f in figures if f.value is not None]

    if len(figures) == 1:
        where = {
            "breach": f"vượt ngưỡng theo dõi ({limit})",
            "early": f"ở vùng cảnh báo sớm (từ {early}), chưa vượt ngưỡng {limit}",
            "ok": f"trong giới hạn so với ngưỡng theo dõi ({limit})",
        }[states[0][1]]
        out.sentences.append(f"{metric_name} {where}.")
        if states[0][1] != "early" and early is not None:
            # The early level is not written, so it is not a figure of this answer.
            out.figures.pop()
        return out

    def listed(wanted: State, with_value: bool) -> str:
        return "; ".join(
            f"{_member(f)} ({{{{{f.key}}}}})" if with_value else _member(f)
            for f, s in states
            if s == wanted
        )

    parts = []
    if over := listed("breach", True):
        parts.append(f"vượt ngưỡng: {over}")
    if warned := listed("early", True):
        parts.append(f"cảnh báo sớm (từ {early}): {warned}")
    if fine := listed("ok", False):
        parts.append(f"trong giới hạn: {fine}")
    if early is not None and not warned:
        out.figures.pop()
    out.sentences.append(f"So với ngưỡng theo dõi ({limit}): {'; '.join(parts)}.")
    return out


async def load(
    org_id: uuid.UUID, space_id: uuid.UUID, metric_id: uuid.UUID, principal: uuid.UUID | None
) -> Threshold | None:
    """The metric's active threshold watch over the whole Data Space, the oldest when several."""
    async with scoped_session(ROLE, org_id, principal) as session:
        rows = (
            await session.execute(
                select(monitor.Monitor.rule, monitor.Monitor.scope_filter)
                .where(
                    monitor.Monitor.data_space_id == space_id,
                    monitor.Monitor.metric_id == metric_id,
                    monitor.Monitor.rule_kind == "threshold",
                    monitor.Monitor.status == "active",
                    monitor.Monitor.deleted_at.is_(None),
                )
                .order_by(monitor.Monitor.created_at)
            )
        ).all()
    # A watch on part of the Data Space (scope_filter) is not this result's threshold.
    whole = (threshold_of(rule) for rule, scope in rows if not scope)
    return next((t for t in whole if t is not None), None)
