"""Phân rã biến động (UC-03-01, skill category `decomposition`): how much each member of a
dimension contributed to a metric's change between two periods. Pure arithmetic over the rows
the semantic queries returned; no model, no sandbox.

Two shapes of metric decompose exactly:
- additive, one summed or counted measure (`du_no`): member i contributes V1_i - V0_i;
- a ratio of two such measures, `A / B * k` (`no_xau / du_no * 100`): the ratio is the
  B-weighted average of the members' own ratios, so member i contributes
  k * (A1_i / ΣB1 - A0_i / ΣB0). Each contribution splits into a rate part (the member's own ratio
  moved: w1_i * (r1_i - r0_i)) and a mix part (its weight in B moved: the rest).

Either way the contributions add up to the change; the result carries that reconciliation, and
says what the numbers are: a contribution by member, not a cause (AC-03-01-07). Anything else (an
average, a difference of measures) is refused rather than approximated.
"""

from __future__ import annotations

import ast
import decimal
from dataclasses import dataclass, field
from typing import Any, Literal

from services.worker_agent.answer.insights import is_additive
from services.worker_agent.answer.narrate import vn_number

MAX_BARS = 8  # the waterfall names the largest contributions; the rest are one "Khác" bar
RECONCILE_TOLERANCE = decimal.Decimal("1e-9")
NO_VALUE = "(không có giá trị)"

D = decimal.Decimal


class NotDecomposable(ValueError):
    """The metric's expression is not one of the shapes that decompose exactly."""


@dataclass(frozen=True)
class Shape:
    kind: Literal["additive", "ratio"]
    numerator: str
    denominator: str | None = None
    scale: decimal.Decimal = field(default_factory=lambda: D(1))


def shape_of(expression: str, measures: dict[str, str]) -> Shape:
    """`du_no` → additive; `no_xau / du_no * 100` (or `100 * a / b`, `a * 100 / b`) → ratio."""
    try:
        node = ast.parse(expression.strip(), mode="eval").body
    except SyntaxError as exc:
        raise NotDecomposable(expression) from exc

    def measure(n: ast.AST) -> str | None:
        return n.id if isinstance(n, ast.Name) and is_additive(n.id, measures) else None

    def const(n: ast.AST) -> decimal.Decimal | None:
        if isinstance(n, ast.Constant) and isinstance(n.value, int | float):
            return D(str(n.value))
        return None

    def ratio(n: ast.AST, scale: decimal.Decimal) -> Shape | None:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            den = measure(n.right)
            if den is None:
                return None
            num = measure(n.left)
            if num is not None:
                return Shape("ratio", num, den, scale)
            # a * k / b
            if isinstance(n.left, ast.BinOp) and isinstance(n.left.op, ast.Mult):
                for a, k in ((n.left.left, n.left.right), (n.left.right, n.left.left)):
                    num, factor = measure(a), const(k)
                    if num is not None and factor is not None:
                        return Shape("ratio", num, den, scale * factor)
        return None

    single = measure(node)
    if single is not None:
        return Shape("additive", single)
    found = ratio(node, D(1))
    if found is None and isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for part, k in ((node.left, node.right), (node.right, node.left)):
            factor = const(k)
            if factor is not None:
                found = ratio(part, factor)
                if found is not None:
                    break
    if found is None:
        raise NotDecomposable(expression)
    return found


@dataclass
class Contribution:
    member: str
    value: decimal.Decimal  # in the metric's unit (points for a ratio)
    rate: decimal.Decimal | None = None  # ratio only
    mix: decimal.Decimal | None = None


@dataclass
class Decomposed:
    base: decimal.Decimal
    current: decimal.Decimal
    contributions: list[Contribution] = field(default_factory=list)

    @property
    def change(self) -> decimal.Decimal:
        return self.current - self.base

    @property
    def residual(self) -> decimal.Decimal:
        return self.change - sum((c.value for c in self.contributions), D(0))


Values = dict[str, tuple[decimal.Decimal, decimal.Decimal | None]]  # member → (numerator, den)


def decompose(shape: Shape, base: Values, current: Values) -> Decomposed:
    members = sorted(set(base) | set(current))
    zero = (D(0), D(0))
    if shape.kind == "additive":
        b0 = sum((base.get(m, zero)[0] for m in members), D(0))
        b1 = sum((current.get(m, zero)[0] for m in members), D(0))
        return Decomposed(
            b0,
            b1,
            [Contribution(m, current.get(m, zero)[0] - base.get(m, zero)[0]) for m in members],
        )
    k = shape.scale
    den0 = sum((base.get(m, zero)[1] or D(0) for m in members), D(0))
    den1 = sum((current.get(m, zero)[1] or D(0) for m in members), D(0))
    if den0 == 0 or den1 == 0:
        raise NotDecomposable("the denominator is zero in one of the periods")
    out = Decomposed(
        k * sum((base.get(m, zero)[0] for m in members), D(0)) / den0,
        k * sum((current.get(m, zero)[0] for m in members), D(0)) / den1,
    )
    for m in members:
        a0, b0_ = base.get(m, zero)
        a1, b1_ = current.get(m, zero)
        b0_, b1_ = b0_ or D(0), b1_ or D(0)
        value = k * (a1 / den1 - a0 / den0)
        # A member present in one period only moved the mix, not a rate of its own.
        rate = k * (b1_ / den1) * (a1 / b1_ - a0 / b0_) if b0_ and b1_ else D(0)
        out.contributions.append(Contribution(m, value, rate, value - rate))
    return out


def _signed(value: decimal.Decimal, decimals: int, unit: str | None) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{vn_number(value, decimals)}{f' {unit}' if unit else ''}"


def _plain(value: decimal.Decimal, decimals: int, unit: str | None) -> str:
    suffix = unit if unit == "%" else f" {unit}" if unit else ""
    return f"{vn_number(value, decimals)}{suffix}"


def result_of(
    d: Decomposed,
    shape: Shape,
    *,
    metric_name: str,
    dimension_name: str,
    unit: str | None,
    decimals: int,
    base_label: str,
    current_label: str,
    skipped: list[str],
) -> dict[str, Any]:
    """The run's result: the conclusion, the waterfall and the table SkillRunPage draws
    (lib/v1/skillAuthoring.ts Decomposition), and the exact numbers behind them."""
    change_unit = "điểm %" if unit == "%" else unit
    ranked = sorted(d.contributions, key=lambda c: abs(c.value), reverse=True)
    change = d.change
    verb = "tăng" if change > 0 else "giảm" if change < 0 else "không đổi"

    def share(c: Contribution) -> str:
        if change == 0:
            return "—"
        return f"{vn_number(c.value / change * 100, 0)}%"

    head = (
        f"{metric_name} {verb} từ {_plain(d.base, decimals, unit)} ({base_label}) "
        f"lên {_plain(d.current, decimals, unit)} ({current_label}), "
        f"{_signed(change, decimals, change_unit)}."
        if change > 0
        else f"{metric_name} {verb} từ {_plain(d.base, decimals, unit)} ({base_label}) "
        f"xuống {_plain(d.current, decimals, unit)} ({current_label}), "
        f"{_signed(change, decimals, change_unit)}."
        if change < 0
        else f"{metric_name} {verb} giữa {base_label} và {current_label}: "
        f"{_plain(d.current, decimals, unit)}."
    )
    sentences = [head]
    top = next((c for c in ranked if c.value != 0), None)
    if top is not None and change != 0:
        sentences.append(
            f"Đóng góp lớn nhất theo {dimension_name.lower()}: {top.member} "
            f"{_signed(top.value, decimals, change_unit)} ({share(top)} mức thay đổi)."
        )
    if shape.kind == "ratio" and change != 0:
        rate = sum((c.rate or D(0) for c in d.contributions), D(0))
        mix = change - rate
        sentences.append(
            f"Trong đó {_signed(rate, decimals, change_unit)} do tỷ lệ trong từng "
            f"{dimension_name.lower()} thay đổi, {_signed(mix, decimals, change_unit)} do cơ cấu "
            f"giữa các {dimension_name.lower()} thay đổi."
        )
    if skipped:
        sentences.append(f"Bỏ qua vì không có dữ liệu: {', '.join(skipped)}.")
    method = (
        "Phân rã kế toán: các phần đóng góp cộng lại đúng bằng mức thay đổi. Đây là phần đóng góp "
        f"theo {dimension_name.lower()}, không phải nguyên nhân; muốn kết luận nguyên nhân cần "
        "kiểm định riêng."
    )
    reconciled = abs(d.residual) <= RECONCILE_TOLERANCE

    shown = ranked[:MAX_BARS]
    rest = ranked[MAX_BARS:]
    bars = [
        {
            "label": c.member,
            "value": float(c.value),
            "value_text": _signed(c.value, decimals, change_unit),
        }
        for c in shown
    ]
    if rest:
        other = sum((c.value for c in rest), D(0))
        bars.append(
            {
                "label": f"Khác ({len(rest)})",
                "value": float(other),
                "value_text": _signed(other, decimals, change_unit),
            }
        )
    return {
        "summary": " ".join(sentences),
        "method": method,
        "confidence": "Chính xác theo phép cộng (đối soát tổng: khớp)"
        if reconciled
        else "Đối soát tổng không khớp: không dùng kết quả này",
        "reconciled": reconciled,
        "shape": shape.kind,
        "skipped": skipped,
        "decomposition": {
            "summary": " ".join(sentences[1:]) or head,
            "start": {
                "label": base_label,
                "value": float(d.base),
                "value_text": _plain(d.base, decimals, unit),
            },
            "steps": bars,
            "end": {
                "label": current_label,
                "value": float(d.current),
                "value_text": _plain(d.current, decimals, unit),
            },
            "contributions": [
                {
                    "label": c.member,
                    "points_text": _signed(c.value, decimals, change_unit),
                    "share_text": share(c),
                }
                for c in ranked
            ],
        },
        # A1.1: the exact numbers, as decimal strings.
        "figures": {
            "base": format(d.base, "f"),
            "current": format(d.current, "f"),
            "change": format(change, "f"),
            "contributions": [
                {
                    "member": c.member,
                    "value": format(c.value, "f"),
                    "rate": format(c.rate, "f") if c.rate is not None else None,
                    "mix": format(c.mix, "f") if c.mix is not None else None,
                }
                for c in ranked
            ],
        },
    }
