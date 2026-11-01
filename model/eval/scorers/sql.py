"""SQL accuracy for the deterministic path, offline and without a model.

What is measured is the route a question takes in production when it names a certified metric
(GEN-42): the Understander's rule pass (worker_agent/understand/resolver.understand) reads the
metric, and the semantic compiler (worker_agent/semantic/compiler.compile_metric) writes the
SQL. That route answers without a model writing arithmetic, so it can be scored exactly on every
pull request, which is the only reason a SQL metric can gate anything without a warehouse and an
LLM in the loop. Until GEN-42 this scored libs/semantic, a compiler production no longer ran.

A catalog (cases/catalogs.json) is a metric contract: each metric is one measure on its table,
with its time column, its dimensions (the cuts its owner allows) and its default filters. The
case's `request` asks for the cuts, grain, period and conditions explicitly, as the plan does
after the readers before the rule pass have taken them out of the question.

Expectations are written against the CONTRACT, not against the compiler's output: required and
forbidden fragments a reader can check by looking at the metric definition. Pasting the
compiler's own output back in as the expectation would produce a test that passes by
construction and notices nothing but change.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from model.eval import _paths

_paths.ensure()

# The day a case is read on: periods in a case are explicit, so it only fixes "tháng này".
TODAY = datetime.date(2026, 10, 15)
_AGGREGATES = {"sum": "SUM({})", "count": "COUNT({})", "count_distinct": "COUNT(DISTINCT {})"}
# The catalog's filter ops in the compiler's words (compiler.FILTER_OPS).
_OPS = {
    "eq": "=",
    "neq": "!=",
    "in": "in",
    "not_in": "not_in",
    "is_null": "is_null",
    "is_not_null": "is_not_null",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "between": "between",
}


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, "eval:" + ":".join(parts))


def _filter(raw: dict[str, Any], dims: dict[str, str]) -> dict[str, Any]:
    """A catalog filter as the compiler's stored filter, on the dimension of its column. A list
    is membership and one value a comparison, as compiler.filter_of reads them."""
    out: dict[str, Any] = {
        "dimension": dims.get(raw["column"], raw["column"]),
        "op": _OPS[raw["op"]],
    }
    if raw.get("value") is not None:
        out["values" if isinstance(raw["value"], list) else "value"] = raw["value"]
    return out


def _build(spec: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    """(vocabulary, layer, metric versions by key) of a catalog. A metric not approved is not in
    force: it is in neither, as the loader leaves a retired or deprecated one out."""
    from libs.language import norm
    from services.worker_agent.semantic import compiler as c
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    live = [m for m in spec["metrics"] if m.get("status", "approved") == "approved"]
    tables: dict[uuid.UUID, Any] = {}
    measures: dict[str, Any] = {}
    dimensions: dict[str, Any] = {}
    versions: dict[str, Any] = {}
    entries: list[Any] = []
    dim_entries: dict[str, Any] = {}
    synonyms: dict[str, tuple[str, ...]] = {}
    for raw in live:
        table = raw["source_table"]
        entity = _id("entity", table)
        tables.setdefault(entity, c.Table(_id("dataset", table), None, table))
        measures[raw["key"]] = c.Measure(
            raw["key"], _AGGREGATES[raw["aggregation"]].format(raw["expression"]), entity
        )
        # One time dimension per table, `period` for the first: the cut's column in the SQL.
        time_code = (
            "period"
            if not dimensions.get("period") or (dimensions["period"].entity_id == entity)
            else f"{table}__period"
        )
        if raw.get("time_column"):
            dimensions.setdefault(
                time_code,
                c.Dimension(
                    _id("dimension", time_code),
                    time_code,
                    "Thời gian",
                    raw["time_column"],
                    entity,
                    is_time=True,
                ),
            )
        by_column: dict[str, str] = {}
        allowed = set()
        # The owner's filters are on dimensions too, ones the metric is not cut by.
        for f in raw.get("default_filters", []):
            code = f"{table}__{f['column']}"
            dimensions.setdefault(
                code, c.Dimension(_id("dimension", code), code, f["column"], f["column"], entity)
            )
            by_column[f["column"]] = code
        for d in raw.get("dimensions", []):
            dim = dimensions.setdefault(
                d["key"],
                c.Dimension(
                    _id("dimension", table, d["key"]),
                    d["key"],
                    d.get("label", d["key"]),
                    d["column"],
                    entity,
                ),
            )
            by_column[d["column"]] = d["key"]
            allowed.add(dim.id)
            dim_entries.setdefault(d["key"], DimensionEntry(dim.id, d["key"], dim.name))
        metric_id = _id("metric", raw["key"])
        versions[raw["key"]] = c.MetricVersion(
            id=_id("version", raw["key"], str(raw["version"])),
            metric_code=raw["key"],
            display_name=raw["name"],
            expression=raw["key"],
            filters=tuple(_filter(f, by_column) for f in raw.get("default_filters", [])),
            unit=raw.get("unit"),
            decimals=2,
            period_semantics="sum_over_period",
            allowed_dimension_ids=frozenset(allowed),
            certified=True,
        )
        entries.append(MetricEntry(metric_id, raw["key"], raw["name"], certified=True))
        for phrase in spec.get("synonyms", {}).get(raw["key"], []):
            synonyms[norm(phrase)] = ("metric", str(metric_id))
    vocab = Vocabulary(metrics=entries, dimensions=list(dim_entries.values()), synonyms=synonyms)
    return vocab, c.Layer(measures, dimensions, tables), versions


def _compile(version: Any, layer: Any, request: dict[str, Any], u: Any) -> Any:
    from services.worker_agent.semantic import compiler as c

    group_by = list(request.get("dimensions", []))
    if request.get("grain"):
        time = c.time_dimension_of(version, layer)
        group_by.append(time.code if time else "")
    period = None
    if request.get("time_range"):
        start, end = (datetime.date.fromisoformat(d) for d in request["time_range"])
        period = c.Period(start, end)
    by_column = {d.expression: d.code for d in layer.dimensions.values()}
    conditions = [c.filter_of(_filter(f, by_column)) for f in request.get("filters", [])]
    return c.compile_metric(
        c.Request(
            version,
            group_by=group_by,
            conditions=conditions,
            period=period,
            grain=request.get("grain"),
            top_n=u.top_n,
            top_desc=u.top_desc,
        ),
        layer,
        dialect=request.get("dialect", "postgres"),
    )


def _guard_verdict(sql: str, dialect: str) -> str:
    """Empty string when the compiled SQL passes sql_guard, else the rejection.

    Compiled SQL is executed through the same read-only path as model-written SQL. A compiler
    that emitted something the guard rejects would break the semantic route in production; here
    it costs one failing case.
    """
    from libs.sql_guard import SqlRejected, check_read_only

    try:
        check_read_only(sql, dialect)
    except SqlRejected as e:
        return str(e)
    return ""


def _check(case: dict[str, Any], catalogs: dict[str, Any]) -> tuple[bool, str, str]:
    """(passed, detail, sql) for one case."""
    from services.worker_agent.semantic.compiler import CompileError
    from services.worker_agent.understand.analysis import find_analysis
    from services.worker_agent.understand.comparison import find_comparison
    from services.worker_agent.understand.grain import find_grain
    from services.worker_agent.understand.resolver import understand

    expect = case["expect"]
    vocab, layer, versions = _build(catalogs[case["catalog"]])
    # The readers questions.py runs before the rule pass take their words out first.
    asked = case["question"]
    for read in (find_comparison, find_grain, find_analysis):
        found = read(asked)
        if found is not None:
            asked = found.rest
    u = understand(asked, vocab, today=TODAY)
    resolved = u.metric.code if u.metric is not None and u.decision != "clarify" else None

    if expect.get("metric") is None:
        if resolved is None:
            return True, f"no metric resolved ({expect.get('reason', '')})", ""
        return False, f"expected no metric, resolved {resolved!r}", ""
    if resolved is None:
        return False, f"expected metric {expect['metric']!r}, resolved nothing ({u.decision})", ""
    if resolved != expect["metric"]:
        return False, f"expected metric {expect['metric']!r}, resolved {resolved!r}", ""

    request = case.get("request", {})
    try:
        compiled = _compile(versions[resolved], layer, request, u)
    except CompileError as e:
        if expect.get("error") == e.code:
            return True, f"{e.code}: {e}", ""
        return False, f"unexpected {e.code}: {e}", ""
    if expect.get("error"):
        return False, f"expected {expect['error']}, compiled instead: {compiled.sql}", compiled.sql

    sql = compiled.sql
    missing = [f for f in expect.get("sql_contains", []) if f not in sql]
    present = [f for f in expect.get("sql_not_contains", []) if f in sql]
    if missing:
        return False, f"missing from SQL: {missing}", sql
    if present:
        return False, f"must not appear in SQL: {present}", sql

    if expect.get("sql_guard_clean"):
        rejection = _guard_verdict(sql, request.get("dialect", "postgres"))
        if rejection:
            return False, f"compiled SQL rejected by sql_guard: {rejection}", sql

    return True, "", sql


def score(cases: list[dict[str, Any]], catalogs: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    correct = 0

    for case in cases:
        passed, detail, sql = _check(case, catalogs)
        correct += 1 if passed else 0
        results.append(
            {
                "id": case["id"],
                "tag": case.get("tag", ""),
                "question": case["question"],
                "passed": passed,
                "detail": detail,
                "sql": sql,
            }
        )

    total = len(cases)
    return {
        "metrics": {"sql_accuracy": (correct / total) if total else 0.0},
        "counts": {"total": total, "correct": correct},
        "results": results,
    }
