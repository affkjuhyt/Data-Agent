"""Deterministic checks that an exploratory statement measures what the question names (F-05, AE-07).

The model audit (agentic/tools/query._audit, RES-02) reads a statement after it ran and can be
talked out of a miss; these rules read it before it runs and cannot. They hold the statement to
the catalogue only where the catalogue speaks: when the deterministic reading of the question
(understand/resolver.understand, the governed path's own pass) names a metric, the statement must
read that metric's measures, all of them, the way they are declared. Each rule is one miss the
DS-AW eval recorded (docs/v3/eval/ds-aw-agent-eval.md, docs/blueprint/evidence/baseline.md):

- R1  reads none of the metric's tables ("Doanh thu năm 2013" answered with the sales quota);
- R1b reads only some of them (revenue of one channel for revenue of both);
- R1c aggregates no column of its measures (a quantity summed for a revenue);
- R2  a value the question filters on is in neither a condition nor a cut ("nhóm hàng Bikes");
- R3  the question names a period and the statement filters nothing;
- R4  the metric divides one measure by another and the statement divides nothing (gross profit
      for gross margin);
- R5  a measure counts distinct values and the statement does not ("Số đơn" as order lines).

Each problem is an instruction for the repair prompt, like check.problems'. Where the catalogue
has no metric for the question, only R2 and R3 apply: the rest has nothing to hold it to.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass

from sqlglot import exp

from libs.sql_guard import expression_columns
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.resolver import understand
from services.worker_agent.understand.text import find_period, norm


def enabled() -> bool:
    """SQL_AUDIT_RULES=off turns the rules off without a deploy (the rollback of AE-07)."""
    return os.getenv("SQL_AUDIT_RULES", "on").lower() != "off"


@dataclass(frozen=True)
class MeasureRead:
    """One measure a metric adds up: the table it lives in and the columns it aggregates."""

    code: str
    table: str
    columns: frozenset[str]
    distinct: bool  # COUNT(DISTINCT …)


@dataclass(frozen=True)
class FilterValue:
    """A value the question filters on: its label and code, either may be written in the SQL."""

    dimension: str
    column: str
    label: str
    words: frozenset[str]  # the label and the code, folded


@dataclass(frozen=True)
class AuditContext:
    metric: str | None  # the display name of the metric the question names, if one
    reads: tuple[MeasureRead, ...] = ()
    ratio: bool = False  # the metric divides one measure by another
    filters: tuple[FilterValue, ...] = ()
    period: bool = False


def context_for(question: str, space: SpaceLayer, *, today: datetime.date) -> AuditContext:
    """What the statement for `question` is held to, read from the Data Space's catalogue."""
    vocab = space.vocabulary
    period = find_period(question, today, vocab.calendar) is not None
    u = understand(question, vocab, today=today)
    filters = _filters(u.filters, space)
    # Only a reading that settles on one metric holds the statement to it: two candidates is
    # the question being asked back, not a measure to check against.
    if u.metric is None or u.decision == "clarify":
        return AuditContext(None, filters=filters, period=period)
    version = space.versions.get(u.metric.metric_id)
    if version is None:
        return AuditContext(None, filters=filters, period=period)
    # A reading that cuts or filters the metric along a dimension the metric does not allow is
    # one the compiler would refuse too ("Doanh số Trade Sales": "doanh số" is a synonym of
    # revenue, Trade Sales an account of the finance table): not a metric to hold SQL to.
    dims = space.layer.dimensions
    used = [dims[code] for code in [*u.group_by, *u.filters] if code in dims]
    if any(not d.is_time and d.id not in version.allowed_dimension_ids for d in used):
        return AuditContext(None, filters=filters, period=period)
    reads: list[MeasureRead] = []
    for code in sorted(expression_columns(version.expression)):
        measure = space.layer.measures.get(code)
        table = space.layer.entity_tables.get(measure.entity_id) if measure else None
        if measure is None or table is None:
            continue
        tree: exp.Expr = exp.maybe_parse(measure.expression, dialect="postgres")
        distinct = any(isinstance(c.this, exp.Distinct) for c in tree.find_all(exp.Count))
        reads.append(
            MeasureRead(
                code,
                table.name,
                frozenset(c.name.lower() for c in tree.find_all(exp.Column)),
                distinct,
            )
        )
    ratio = exp.maybe_parse(version.expression, dialect="postgres").find(exp.Div) is not None
    return AuditContext(u.metric.name, tuple(reads), ratio, filters, period)


def _filters(chosen: dict[str, list[str]], space: SpaceLayer) -> tuple[FilterValue, ...]:
    dims = space.layer.dimensions
    labels = {(v.dimension_code, v.value_code): v.label for v in space.vocabulary.values}
    out: list[FilterValue] = []
    for code, members in chosen.items():
        dim = dims.get(code)
        if dim is None:
            continue
        cols = expression_columns(dim.expression)
        column = next(iter(cols)).lower() if len(cols) == 1 else ""
        for member in members:
            label = labels.get((code, member), member)
            words = {norm(member), norm(label)}
            out.append(FilterValue(dim.name, column, label, frozenset(w for w in words if w)))
    return tuple(out)


def check(qualified: exp.Expr, ctx: AuditContext) -> list[str]:
    """The rules a statement breaks, as instructions; `qualified` is check.problems' qualified
    tree, so every column carries its table and every name is folded as the dialect folds it."""
    tables = {t.name.lower() for t in qualified.find_all(exp.Table)}
    found: list[str] = []
    if ctx.reads:
        found += _measures(qualified, tables, ctx)
    found += _filters_dropped(qualified, ctx)
    if ctx.period and not (qualified.find(exp.Where) or qualified.find(exp.Having)):
        found.append(
            "Câu hỏi nêu một kỳ nhưng SQL không lọc theo thời gian; thêm điều kiện theo cột ngày "
            "của bảng."
        )
    return found


def _measures(qualified: exp.Expr, tables: set[str], ctx: AuditContext) -> list[str]:
    want = {r.table.lower() for r in ctx.reads}
    name = ctx.metric or "chỉ số"
    where = ", ".join(sorted(want))
    if not tables & want:
        return [
            f"Câu hỏi hỏi '{name}', chỉ số này đọc từ bảng {where}; SQL không đọc bảng nào trong "
            "đó, tức là đang tính một đại lượng khác. Viết lại trên đúng các bảng này."
        ]
    found: list[str] = []
    missing = sorted(want - tables)
    # Without a value filter every table of the metric holds part of the answer ("Doanh thu năm
    # 2013" read on one channel). A filtered slice may live in one table only (Components are
    # sold to resellers alone), which the data knows and this rule cannot.
    if missing and not ctx.filters:
        found.append(
            f"'{name}' cộng số của các bảng {where}; SQL bỏ sót {', '.join(missing)}, nên chỉ "
            "ra một phần của chỉ số. Đọc đủ các bảng (cộng từng bảng riêng rồi cộng lại)."
        )
    aggregated = {
        c.name.lower() for agg in qualified.find_all(exp.AggFunc) for c in agg.find_all(exp.Column)
    }
    allowed = set().union(*(r.columns for r in ctx.reads))
    if qualified.find(exp.AggFunc) and aggregated and not aggregated & allowed:
        found.append(
            f"'{name}' tổng hợp cột {', '.join(sorted(allowed))}; SQL tổng hợp "
            f"{', '.join(sorted(aggregated))}, là một đại lượng khác."
        )
    if ctx.ratio and qualified.find(exp.Div) is None:
        found.append(
            f"'{name}' là một tỷ lệ (chia hai đại lượng trên tổng); SQL không có phép chia. "
            "Tính tử số và mẫu số trên tổng rồi chia."
        )
    counted = {
        c.name.lower()
        for count in qualified.find_all(exp.Count)
        if isinstance(count.this, exp.Distinct)
        for c in count.this.find_all(exp.Column)
    }
    for r in ctx.reads:
        if r.distinct and not counted & r.columns:
            found.append(
                f"'{name}' đếm số giá trị khác nhau của {', '.join(sorted(r.columns))} "
                "(COUNT(DISTINCT …)); SQL đếm số dòng, một giá trị nhiều dòng sẽ bị đếm nhiều lần."
            )
    return found


def _filters_dropped(qualified: exp.Expr, ctx: AuditContext) -> list[str]:
    if not ctx.filters:
        return []
    literals = [norm(str(lit.this)) for lit in qualified.find_all(exp.Literal) if lit.is_string]
    grouped = {
        c.name.lower()
        for group in qualified.find_all(exp.Group)
        for c in group.find_all(exp.Column)
    }
    found: list[str] = []
    for f in ctx.filters:
        said = any(w and (w == lit or w in lit) for w in f.words for lit in literals)
        if said or (f.column and f.column in grouped):
            continue
        found.append(
            f"Câu hỏi lọc {f.dimension} = '{f.label}' nhưng SQL không có điều kiện đó và không chia "
            "theo nó; kết quả sẽ là tổng của mọi giá trị. Thêm điều kiện lọc."
        )
    return found
