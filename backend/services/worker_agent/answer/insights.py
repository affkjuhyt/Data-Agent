"""What a single-metric result says, worked out by arithmetic, not by a model (tab 5 G5.15).

The analyst's reading of a table or chart: which member is highest and lowest and by how much,
what share the largest holds of the total, which member stands apart, and along time how the last
period moved against the one before. Each number this needs is a new figure F<n+1>… whose source
is an expression over the result's figures ({compute_run_id, expr}), so it is traced like any
other: the text writes it as a tag, the receipt keeps the expression's hash, and a replay
recomputes it with `evaluate` from the replayed figures.

Rules that keep the reading honest:
- nothing is derived when a figure is masked or missing (BR-178): a total or a gap would reveal
  the hidden number;
- a share of the total only for an additive metric (one measure summed or counted). A ratio or an
  average does not add up across members (the "trung bình tỷ lệ" trap), so it gets no total;
- a change of a percentage is in percentage points, never a percent of a percent;
- a member "stands apart" by the median absolute deviation (|robust z| > 3.5, five members at
  least), which says nothing about why.
"""

from __future__ import annotations

import ast
import decimal
import re
import statistics
from dataclasses import dataclass, field

from services.worker_agent.answer.narrate import Figure, shown_value

PERCENT_DECIMALS = 1
MIN_FOR_OUTLIER = 5
OUTLIER_Z = decimal.Decimal("3.5")
# 0.6745 makes the MAD comparable to a standard deviation under normality (Iglewicz & Hoaglin).
MAD_SCALE = decimal.Decimal("0.6745")

_TAG = re.compile(r"\{\{\s*(F\d+)\s*\}\}")
_ADDITIVE = re.compile(r"^\s*(?:SUM|COUNT)\s*\(", re.IGNORECASE)
_CODE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Insights:
    figures: list[Figure] = field(default_factory=list)
    # Sentences with figure tags {{F<n>}}, for the template text and as hints to the Narrator.
    sentences: list[str] = field(default_factory=list)


class ExprError(ValueError):
    """An expression outside the arithmetic `evaluate` accepts."""


def evaluate(expr: str, values: dict[str, decimal.Decimal]) -> decimal.Decimal:
    """`(F3 - F2) / F2 * 100` over the figures' values: + - * /, parentheses, numbers, F<n>."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(expr) from exc

    def walk(node: ast.AST) -> decimal.Decimal:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return decimal.Decimal(str(node.value))
        raise ExprError(expr)

    return walk(tree)


def written(sentences: list[str], figures: list[Figure]) -> str:
    """The sentences with each tag replaced by its figure's value, for the template text."""
    by_key = {f.key: f for f in figures if f.value is not None}
    return " ".join(_TAG.sub(lambda m: shown_value(by_key[m.group(1)]), s) for s in sentences)


def is_additive(expression: str, measures: dict[str, str]) -> bool:
    """One measure code whose aggregate is SUM or COUNT adds up across members."""
    code = expression.strip()
    return bool(_CODE.match(code)) and bool(_ADDITIVE.match(measures.get(code, "")))


class _Builder:
    def __init__(self, base: list[Figure], unit: str | None, decimals: int) -> None:
        self.base = base
        self.unit = unit
        self.decimals = decimals
        self.values = {f.key: f.value for f in base if f.value is not None}
        self.out = Insights()

    def add(
        self, label: str, expr: str, *, unit: str | None, decimals: int, signed: bool = False
    ) -> str | None:
        """The new figure's tag, or None when the expression cannot be computed (÷ 0)."""
        try:
            value = evaluate(expr, self.values)
        except (ExprError, decimal.DivisionByZero, decimal.InvalidOperation):
            return None
        key = f"F{len(self.base) + len(self.out.figures) + 1}"
        self.out.figures.append(
            Figure(key, label, value, unit, decimals, -1, "value", expr=expr, signed=signed)
        )
        return f"{{{{{key}}}}}"


def _member(f: Figure, code: str) -> str:
    return f.dimensions.get(code, f.key)


def _tag(f: Figure) -> str:
    return f"{{{{{f.key}}}}}"


def insights_of(
    figures: list[Figure],
    *,
    group_by: list[str],
    time_dimensions: set[str],
    metric_name: str,
    unit: str | None,
    decimals: int,
    additive: bool,
) -> Insights:
    """The derived figures and sentences for a result cut along one dimension."""
    if len(group_by) != 1 or len(figures) < 2:
        return Insights()
    if any(f.display_state != "shown" or f.value is None for f in figures):
        return Insights()
    code = group_by[0]
    b = _Builder(figures, unit, decimals)
    if code in time_dimensions:
        _over_time(b, code, metric_name)
    else:
        _across_members(b, code, metric_name, additive)
    return b.out


def _change_unit(unit: str | None) -> str | None:
    return "điểm %" if unit == "%" else unit


def _over_time(b: _Builder, code: str, metric_name: str) -> None:
    # Period members sort as text (2026-08 < 2026-09); the compiler orders them the same way.
    ordered = sorted(b.base, key=lambda f: _member(f, code))
    last, prev = ordered[-1], ordered[-2]
    when, before = _member(last, code), _member(prev, code)
    delta = b.add(
        f"{metric_name}: thay đổi {when} so với {before}",
        f"{last.key} - {prev.key}",
        unit=_change_unit(b.unit),
        decimals=b.decimals,
        signed=True,
    )
    if delta is None:
        return
    pct = None
    if b.unit != "%":
        pct = b.add(
            f"{metric_name}: tỷ lệ thay đổi {when} so với {before}",
            f"({last.key} - {prev.key}) / {prev.key} * 100",
            unit="%",
            decimals=PERCENT_DECIMALS,
            signed=True,
        )
    assert last.value is not None and prev.value is not None
    verb = "tăng" if last.value > prev.value else "giảm" if last.value < prev.value else "đi ngang"
    change = f"{delta} ({pct})" if pct else delta
    b.out.sentences.append(f"{when} {verb} so với {before}: {change}.")
    if len(ordered) >= 3:
        high = max(ordered, key=lambda f: f.value or 0)
        low = min(ordered, key=lambda f: f.value or 0)
        if high is not low:
            b.out.sentences.append(
                f"Trong {len(ordered)} kỳ, cao nhất là {_member(high, code)} ({_tag(high)}), "
                f"thấp nhất là {_member(low, code)} ({_tag(low)})."
            )


def _across_members(b: _Builder, code: str, metric_name: str, additive: bool) -> None:
    high = max(b.base, key=lambda f: f.value or 0)
    low = min(b.base, key=lambda f: f.value or 0)
    if high.value == low.value:
        b.out.sentences.append("Các nhóm bằng nhau.")
        return
    gap = b.add(
        f"{metric_name}: chênh lệch giữa {_member(high, code)} và {_member(low, code)}",
        f"{high.key} - {low.key}",
        unit=_change_unit(b.unit),
        decimals=b.decimals,
    )
    sentence = (
        f"Cao nhất là {_member(high, code)} ({_tag(high)}), "
        f"thấp nhất là {_member(low, code)} ({_tag(low)})"
    )
    b.out.sentences.append(f"{sentence}, chênh {gap}." if gap else f"{sentence}.")
    if additive and all((f.value or 0) >= 0 for f in b.base):
        total_expr = " + ".join(f.key for f in b.base)
        total = b.add(f"{metric_name}: tổng", total_expr, unit=b.unit, decimals=b.decimals)
        share = b.add(
            f"{metric_name}: tỷ trọng của {_member(high, code)} trong tổng",
            f"{high.key} / ({total_expr}) * 100",
            unit="%",
            decimals=PERCENT_DECIMALS,
        )
        if total and share:
            b.out.sentences.append(f"Tổng là {total}; {_member(high, code)} chiếm {share}.")
    apart = _standing_apart(b.base)
    if apart:
        names = ", ".join(_member(f, code) for f in apart)
        b.out.sentences.append(f"{names} khác hẳn các nhóm còn lại.")


def _standing_apart(figures: list[Figure]) -> list[Figure]:
    if len(figures) < MIN_FOR_OUTLIER:
        return []
    values = [f.value for f in figures if f.value is not None]
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values)
    if mad == 0:
        return []
    return [
        f
        for f in figures
        if f.value is not None and abs(MAD_SCALE * (f.value - median) / mad) > OUTLIER_Z
    ]
