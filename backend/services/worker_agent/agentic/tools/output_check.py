"""What the analysis code computes, checked for meaning (AL-06, ANALYTICS_SPEC §B.2 step 5).

The sandbox makes the code safe to run; it does not make its numbers mean anything. Summing a
ratio (the conversion rates of three regions added up), a share of a distinct count, a running
total of an average, revenue in billions plus a count of orders: each runs, each gives a number,
each is wrong. Deterministic checks, no model:

- before the code runs, from its AST and the SQL it passes to sql(): a sum, a running total or a
  share over a dataset whose metric does not add up (its reading says `additivity: none`), and
  an addition or a difference between columns of two datasets in different units;
- after it runs, against what the model declared it would return (`outputs`): every declared
  column is there, a column declared as a sum, a share or a mean is numeric, and a sum or a
  difference is not drawn from inputs in different units (a ratio may be).

Every refusal is CONTRACT_MISMATCH with a reason the model can act on once (the tool's
`arguments` failure); UNIT_MISMATCH and NON_ADDITIVE say which rule. Nothing here guesses: a
dataset with no reading (the model's own SQL) has no declared additivity and is not refused for
it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from services.worker_agent.agentic.workspace import Dataset, number

Op = Literal["sum", "share", "cumulative", "mean", "count", "ratio", "difference", "rank", "other"]
ADDING = frozenset({"sum", "share", "cumulative"})
NUMERIC = frozenset({"sum", "share", "cumulative", "mean", "count", "ratio", "difference"})
# Methods that add the rows they are called on.
_SUMS = frozenset({"sum", "cumsum"})
_SQL_SUM = re.compile(r"\b(sum|cumsum)\s*\(", re.IGNORECASE)
_SQL_TABLE = re.compile(r"\b(?:from|join)\s+(\w+)", re.IGNORECASE)


class OutputColumn(BaseModel):
    """One column the code says it returns."""

    name: str
    op: Op = "other"
    unit: str | None = None
    # The inputs it is computed from, "d1.Doanh thu"; their datasets' units and additivity apply.
    derived_from: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Mismatch:
    code: Literal["CONTRACT_MISMATCH", "UNIT_MISMATCH", "NON_ADDITIVE"]
    detail: str

    def text(self) -> str:
        return f"{self.code}: {self.detail}"


def _non_additive(ds: Dataset) -> bool:
    return (ds.reading or {}).get("additivity") == "none"


def _metric(ds: Dataset) -> str:
    return str((ds.reading or {}).get("metric_name") or ds.title)


def _dataset_of(node: ast.AST) -> str | None:
    """The dataset name an expression starts from: d1['x'].sum() → d1, d1.groupby(…)[…] → d1."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute | ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """`x = d1[...]`, `g = d1.groupby(...)`: a name for an expression over a dataset."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            source = _dataset_of(node.value)
            if isinstance(target, ast.Name) and source:
                found[target.id] = source
    return found


def _origin(
    name: str | None, aliases: Mapping[str, str], datasets: Mapping[str, Dataset]
) -> str | None:
    for _ in range(6):
        if name is None or name in datasets:
            return name
        name = aliases.get(name)
    return None


def tests_of(code: str) -> list[str]:
    """The statistical tests the code imports from scipy.stats (AL-06): its result is an
    inference."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return sorted(
        {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "scipy.stats"
            for alias in node.names
        }
    )


def before_run(code: str, datasets: Mapping[str, Dataset]) -> list[Mismatch]:
    """What the code would compute wrong, read from its text before it runs."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # the sandbox's own check says why
    aliases = _aliases(tree)
    found: list[Mismatch] = []
    seen: set[tuple[str, str]] = set()

    def add(m: Mismatch) -> None:
        if (m.code, m.detail) not in seen:
            seen.add((m.code, m.detail))
            found.append(m)

    for node in ast.walk(tree):
        # pandas: d1['x'].sum(), d1.groupby('v')['x'].cumsum(), x / x.sum(), .agg('sum')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            summed = method in _SUMS or (
                method in ("agg", "aggregate", "transform")
                and any(isinstance(a, ast.Constant) and a.value in _SUMS for a in node.args)
            )
            origin = _origin(_dataset_of(node.func.value), aliases, datasets)
            if summed and origin and _non_additive(datasets[origin]):
                add(
                    Mismatch(
                        "NON_ADDITIVE",
                        f"{origin} là “{_metric(datasets[origin])}”, không cộng được giữa các dòng:"
                        " không lấy tổng, luỹ kế hay tỷ trọng; dùng trung bình hoặc truy vấn lại"
                        " chỉ số ở mức tổng",
                    )
                )
        # SQL handed to sql(): SUM( over a table that does not add up.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sql"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            query = node.args[0].value
            tables = {t for t in _SQL_TABLE.findall(query) if t in datasets}
            if _SQL_SUM.search(query):
                for t in sorted(tables):
                    if _non_additive(datasets[t]):
                        add(
                            Mismatch(
                                "NON_ADDITIVE",
                                f"{t} là “{_metric(datasets[t])}”, không cộng được: SUM trên nó"
                                " không ra tổng; dùng AVG hoặc truy vấn lại chỉ số ở mức tổng",
                            )
                        )
        # d1['Doanh thu'] + d2['Số đơn']: two datasets in two units added or subtracted.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub):
            left = _origin(_dataset_of(node.left), aliases, datasets)
            right = _origin(_dataset_of(node.right), aliases, datasets)
            if left and right and left != right:
                lu, ru = datasets[left].unit, datasets[right].unit
                if lu and ru and lu != ru:
                    add(
                        Mismatch(
                            "UNIT_MISMATCH",
                            f"cộng hoặc trừ {left} ({lu}) với {right} ({ru}): khác đơn vị;"
                            " chỉ chia (tỷ lệ) hai đại lượng khác đơn vị",
                        )
                    )
    return found


def after_run(
    outputs: Sequence[OutputColumn],
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    datasets: Mapping[str, Dataset],
) -> list[Mismatch]:
    """The result against what the code declared it would return."""
    found: list[Mismatch] = []
    for out in outputs:
        if out.name not in columns:
            found.append(
                Mismatch(
                    "CONTRACT_MISMATCH",
                    f"kết quả không có cột “{out.name}” đã khai; các cột: {list(columns)}",
                )
            )
            continue
        i = list(columns).index(out.name)
        values = [r[i] for r in rows if i < len(r) and r[i] is not None]
        if out.op in NUMERIC and values and any(number(v) is None for v in values):
            found.append(
                Mismatch(
                    "CONTRACT_MISMATCH",
                    f"cột “{out.name}” khai là {out.op} nhưng có giá trị không phải số",
                )
            )
        sources = [s.split(".", 1)[0] for s in out.derived_from]
        known = [datasets[s] for s in sources if s in datasets]
        units = {d.unit for d in known if d.unit}
        if out.op in ("sum", "difference") and len(units) > 1:
            found.append(
                Mismatch(
                    "UNIT_MISMATCH",
                    f"cột “{out.name}” là {out.op} của các đơn vị khác nhau ({', '.join(sorted(units))})",
                )
            )
        if out.op in ADDING:
            for ds_name, ds in zip(sources, known, strict=False):
                if _non_additive(ds):
                    found.append(
                        Mismatch(
                            "NON_ADDITIVE",
                            f"cột “{out.name}” là {out.op} của {ds_name} (“{_metric(ds)}”),"
                            " một chỉ số không cộng được",
                        )
                    )
    return found
