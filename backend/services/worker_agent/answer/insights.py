"""What a single-metric result says, worked out by arithmetic, not by a model (tab 5 G5.15).

The analyst's reading of a table or chart: which member is highest and lowest, by how much and how
many times over, what share the largest (and the three largest) hold of the total, whether the
members fall off evenly or the first few stand clear of the rest, which member stands apart; and
along time how the last period moved against the one before and against the first, the average
move per period, and how many periods in a row it has gone the same way. Each number this needs is a new figure F<n+1>… whose source
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
  least), which says nothing about why;
- "how many times" only when every value is positive: a ratio to a negative or zero value reads
  as nonsense;
- a count of members or periods is written in words, never digits: the Narrator's post-check
  accepts a number only when it is a figure's value (answer/narrator.py).
"""

from __future__ import annotations

import ast
import decimal
import itertools
import re
import statistics
from dataclasses import dataclass, field

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from services.worker_agent.answer.narrate import Figure, shown_value

PERCENT_DECIMALS = 1
MIN_FOR_OUTLIER = 5
OUTLIER_Z = decimal.Decimal("3.5")
# 0.6745 makes the MAD comparable to a standard deviation under normality (Iglewicz & Hoaglin).
MAD_SCALE = decimal.Decimal("0.6745")
TIMES_DECIMALS = 1
MIN_FOR_TOP = 5  # "the three largest hold X%" says something only when there are others
TOP = 3
MIN_FOR_SHAPE = 5
# Sorted from the largest, the members "break" after the k-th when the gap between the k-th and
# the next is at least this share of the whole range; they fall off "evenly" when no gap is
# above EVEN_GAP of it. In between, the reading says nothing about the shape.
BREAK_GAP = decimal.Decimal("0.4")
EVEN_GAP = decimal.Decimal("0.25")
MIN_FOR_TREND = 4
MIN_STREAK = 3

_COUNT_WORDS = {
    2: "hai",
    3: "ba",
    4: "bốn",
    5: "năm",
    6: "sáu",
    7: "bảy",
    8: "tám",
    9: "chín",
    10: "mười",
    11: "mười một",
    12: "mười hai",
}

_TAG = re.compile(r"\{\{\s*(F\d+)\s*\}\}")
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
    """One measure code whose aggregate is SUM or COUNT (not DISTINCT) adds up across members.

    Read from the AST, as semantic/analysis.is_additive does: a prefix regex took
    COUNT(DISTINCT x) for additive and produced shares of distinct counts."""
    code = expression.strip()
    if not _CODE.match(code) or code not in measures:
        return False
    try:
        measure = parse_one(measures[code])
    except ParseError:
        return False
    if isinstance(measure, exp.Sum):
        return True
    return isinstance(measure, exp.Count) and not isinstance(measure.this, exp.Distinct)


class Builder:
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
    b = Builder(figures, unit, decimals)
    if code in time_dimensions:
        _over_time(b, code, metric_name)
    else:
        _across_members(b, code, metric_name, additive)
    return b.out


def _change_unit(unit: str | None) -> str | None:
    return "điểm %" if unit == "%" else unit


def _over_time(b: Builder, code: str, metric_name: str) -> None:
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
                f"Trong {_count(len(ordered))} kỳ, cao nhất là {_member(high, code)} "
                f"({_tag(high)}), thấp nhất là {_member(low, code)} ({_tag(low)})."
            )
    if len(ordered) >= MIN_FOR_TREND:
        _whole_span(b, ordered, code, metric_name)
        _trend(b, ordered, metric_name)
        _streak(b, ordered, code, metric_name)


def _whole_span(b: Builder, ordered: list[Figure], code: str, metric_name: str) -> None:
    first, last = ordered[0], ordered[-1]
    since, when = _member(first, code), _member(last, code)
    delta = b.add(
        f"{metric_name}: thay đổi {when} so với {since}",
        f"{last.key} - {first.key}",
        unit=_change_unit(b.unit),
        decimals=b.decimals,
        signed=True,
    )
    if delta is None:
        return
    pct = None
    if b.unit != "%":
        pct = b.add(
            f"{metric_name}: tỷ lệ thay đổi {when} so với {since}",
            f"({last.key} - {first.key}) / {first.key} * 100",
            unit="%",
            decimals=PERCENT_DECIMALS,
            signed=True,
        )
    change = f"{delta} ({pct})" if pct else delta
    b.out.sentences.append(f"Từ {since} đến {when}: {change}.")


def _trend(b: Builder, ordered: list[Figure], metric_name: str) -> None:
    """The least-squares slope over the periods taken as 0…n-1, as one expression over the
    figures: slope = Σ (2i - (n-1)) · y_i / (n(n²-1)/6). The weights are integers and n(n²-1) is
    a product of three consecutive integers, so the divisor is one too and `evaluate` takes it."""
    n = len(ordered)
    terms = " + ".join(f"{2 * i - (n - 1)} * {f.key}" for i, f in enumerate(ordered))
    slope = b.add(
        f"{metric_name}: thay đổi bình quân mỗi kỳ",
        f"({terms}) / {n * (n * n - 1) // 6}",
        unit=_change_unit(b.unit),
        decimals=b.decimals,
        signed=True,
    )
    if slope is None:
        return
    value = b.out.figures[-1].value
    assert value is not None
    if value == 0:
        b.out.sentences.append(f"Qua {_count(n)} kỳ, xu hướng đi ngang.")
    else:
        verb = "tăng" if value > 0 else "giảm"
        b.out.sentences.append(f"Qua {_count(n)} kỳ, bình quân mỗi kỳ {verb} {slope}.")


def _streak(b: Builder, ordered: list[Figure], code: str, metric_name: str) -> None:
    # How many moves in a row, ending at the last period, went the same way.
    moves = [(cur.value or 0) - (prev.value or 0) for prev, cur in itertools.pairwise(ordered)]
    if not moves or moves[-1] == 0:
        return
    rising = moves[-1] > 0
    length = 0
    for move in reversed(moves):
        if move == 0 or (move > 0) != rising:
            break
        length += 1
    if length >= MIN_STREAK:
        verb = "tăng" if rising else "giảm"
        when = _member(ordered[-1], code)
        b.out.sentences.append(f"{metric_name} {verb} {_count(length)} kỳ liên tiếp tới {when}.")


def _count(n: int) -> str:
    return _COUNT_WORDS.get(n, "nhiều")


def _across_members(b: Builder, code: str, metric_name: str, additive: bool) -> None:
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
    if len(b.base) >= MIN_FOR_TREND and all((f.value or 0) > 0 for f in b.base):
        times = b.add(
            f"{metric_name}: {_member(high, code)} so với {_member(low, code)}",
            f"{high.key} / {low.key}",
            unit="lần",
            decimals=TIMES_DECIMALS,
        )
        if times:
            b.out.sentences.append(f"{_member(high, code)} gấp {times} {_member(low, code)}.")
    ranked = sorted(b.base, key=lambda f: f.value or 0, reverse=True)
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
        if len(ranked) >= MIN_FOR_TOP:
            top = ranked[:TOP]
            top_share = b.add(
                f"{metric_name}: tỷ trọng của {_count(TOP)} nhóm lớn nhất trong tổng",
                f"({' + '.join(f.key for f in top)}) / ({total_expr}) * 100",
                unit="%",
                decimals=PERCENT_DECIMALS,
            )
            if top_share:
                names = ", ".join(_member(f, code) for f in top)
                b.out.sentences.append(
                    f"{_count(TOP).capitalize()} nhóm lớn nhất ({names}) chiếm {top_share}."
                )
    apart = _standing_apart(b.base)
    if apart:
        names = ", ".join(_member(f, code) for f in apart)
        b.out.sentences.append(f"{names} khác hẳn các nhóm còn lại.")
    elif len(ranked) >= MIN_FOR_SHAPE:
        _shape(b, ranked, code, metric_name)


def _shape(b: Builder, ranked: list[Figure], code: str, metric_name: str) -> None:
    """Whether the members, from the largest, fall off evenly or break after the first few. Read
    only when no member stands apart: an outlier is its own, stronger sentence."""
    values = [f.value or decimal.Decimal(0) for f in ranked]
    spread = values[0] - values[-1]
    if spread <= 0:
        return
    gaps = [values[i] - values[i + 1] for i in range(len(values) - 1)]
    widest = max(range(len(gaps)), key=lambda i: gaps[i])
    if gaps[widest] >= BREAK_GAP * spread and widest + 1 < len(ranked) - 1:
        above, below = ranked[widest], ranked[widest + 1]
        gap = b.add(
            f"{metric_name}: khoảng cách giữa {_member(above, code)} và {_member(below, code)}",
            f"{above.key} - {below.key}",
            unit=_change_unit(b.unit),
            decimals=b.decimals,
        )
        if gap:
            lead = (
                _member(above, code)
                if widest == 0
                else f"{_count(widest + 1).capitalize()} nhóm đầu"
                + (
                    f" ({', '.join(_member(f, code) for f in ranked[: widest + 1])})"
                    if widest + 1 <= TOP
                    else ""
                )
            )
            b.out.sentences.append(
                f"{lead} tách khỏi phần còn lại: {_member(above, code)} hơn"
                f" {_member(below, code)} {gap}."
            )
    elif max(gaps) <= EVEN_GAP * spread:
        b.out.sentences.append("Các nhóm giảm dần đều, không có nhóm nào tách hẳn.")


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
