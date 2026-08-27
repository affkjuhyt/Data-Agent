"""SQL accuracy for the deterministic path — offline, no model involved.

What is measured is the whole semantic route a question takes when it resolves
to a registered metric: natural language -> MetricResolver -> compile_metric ->
SQL. That route answers without a model writing arithmetic, so it can be scored
exactly and on every pull request, which is the only reason a SQL metric can gate
anything without a warehouse and an LLM in the loop.

Expectations are written against the CONTRACT, not against the compiler's
output: required and forbidden fragments a reader can check by looking at the
metric definition. Pasting the compiler's own output back in as the expectation
would produce a test that passes by construction and notices nothing but change.

The model-written SQL path (handlers/data.py's text_to_sql) is the online tier's
job — it needs a model and a warehouse, and it is scored by execution, not by
string comparison.
"""
from __future__ import annotations

from typing import Any

from model.eval import _paths

_paths.ensure(_paths.SANDBOX_EXECUTOR)


def _build_catalog(spec: dict[str, Any]) -> Any:
    from libs.semantic.models import (
        Aggregation,
        MetricDefinition,
        MetricDimension,
        MetricFilter,
        MetricStatus,
        TimeGrain,
    )
    from libs.semantic.resolver import build_catalog

    definitions = []
    for raw in spec["metrics"]:
        definitions.append(MetricDefinition(
            key=raw["key"],
            version=int(raw["version"]),
            name=raw["name"],
            aggregation=Aggregation(raw["aggregation"]),
            source_table=raw["source_table"],
            expression=raw["expression"],
            time_column=raw.get("time_column", ""),
            default_grain=TimeGrain(raw.get("default_grain", "none")),
            unit=raw.get("unit", ""),
            dimensions=tuple(MetricDimension.from_dict(d) for d in raw.get("dimensions", [])),
            default_filters=tuple(MetricFilter.from_dict(f) for f in raw.get("default_filters", [])),
            status=MetricStatus(raw.get("status", "approved")),
            owner=raw.get("owner", ""),
            description=raw.get("description", ""),
        ))
    return build_catalog(definitions, spec.get("synonyms", {}))


def _compile(definition: Any, request: dict[str, Any]) -> Any:
    from libs.semantic.compiler import DEFAULT_LIMIT, compile_metric
    from libs.semantic.models import MetricFilter, TimeGrain

    grain = TimeGrain(request["grain"]) if request.get("grain") else None
    time_range = tuple(request["time_range"]) if request.get("time_range") else None
    extra_filters = [MetricFilter.from_dict(f) for f in request.get("filters", [])]

    return compile_metric(
        definition,
        dialect=request.get("dialect", "postgres"),
        dimensions=request.get("dimensions"),
        grain=grain,
        time_range=time_range,
        extra_filters=extra_filters,
        limit=int(request.get("limit", DEFAULT_LIMIT)),
    )


def _guard_verdict(sql: str, dialect: str) -> str:
    """Empty string when the compiled SQL passes sql_guard, else the rejection.

    Compiled SQL is executed through the same read-only path as model-written
    SQL. A compiler that emitted something the guard rejects would break the
    semantic route in production; here it costs one failing case.
    """
    from sql_guard import SQLGuardError, validate_select_only

    try:
        validate_select_only(sql, dialect if dialect in ("postgres", "clickhouse") else None)
    except SQLGuardError as e:
        return str(e)
    return ""


def _check(case: dict[str, Any], catalogs: dict[str, Any]) -> tuple[bool, str, str]:
    """(passed, detail, sql) for one case."""
    from libs.semantic.models import SemanticError
    from libs.semantic.resolver import MetricResolver

    expect = case["expect"]
    catalog = _build_catalog(catalogs[case["catalog"]])
    resolution = MetricResolver(catalog).resolve(case["question"])

    if expect.get("metric") is None:
        if resolution is None:
            return True, f"no metric resolved ({expect.get('reason', '')})", ""
        return False, f"expected no metric, resolved {resolution.definition.key!r}", ""

    if resolution is None:
        return False, f"expected metric {expect['metric']!r}, resolved nothing", ""
    if resolution.definition.key != expect["metric"]:
        return False, f"expected metric {expect['metric']!r}, resolved {resolution.definition.key!r}", ""

    request = case.get("request", {})
    if expect.get("error"):
        try:
            compiled = _compile(resolution.definition, request)
        except SemanticError as e:
            actual = type(e).__name__
            if actual == expect["error"]:
                return True, f"{actual}: {e}", ""
            return False, f"expected {expect['error']}, raised {actual}: {e}", ""
        return False, f"expected {expect['error']}, compiled instead: {compiled.sql}", compiled.sql

    try:
        compiled = _compile(resolution.definition, request)
    except SemanticError as e:
        return False, f"unexpected {type(e).__name__}: {e}", ""

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
        results.append({
            "id": case["id"],
            "tag": case.get("tag", ""),
            "question": case["question"],
            "passed": passed,
            "detail": detail,
            "sql": sql,
        })

    total = len(cases)
    return {
        "metrics": {"sql_accuracy": (correct / total) if total else 0.0},
        "counts": {"total": total, "correct": correct},
        "results": results,
    }
