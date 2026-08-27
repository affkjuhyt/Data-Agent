"""Semantic Layer for workflow-runtime — loads metric contracts, resolves a
question to one, compiles it to SQL.

The pure logic lives in `libs/semantic` (leaf layer, no I/O). This module is the
service-side half: Postgres loading, per-tenant caching, and the one entry point
the DB Connector calls.

Why this exists at all: before it, "doanh thu tháng 6" produced whatever SQL the
model wrote that turn. Gross and net revenue were the same question, `COUNT(*)`
and `COUNT(DISTINCT ...)` were interchangeable, and the month came from whichever
date column the model noticed first. A resolved metric replaces all of that with
one approved formula, so the same question gives the same number and the answer
can say which contract produced it.

Resolution failing is normal and not an error: most questions are not about a
registered metric, and the caller keeps its existing LLM path for those. This
layer only claims the questions it can answer exactly.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from libs.semantic import (
    Aggregation,
    ambiguity,
    AnalyticalSlots,
    InvalidMetricDefinition,
    MetricDefinition,
    MetricDimension,
    MetricFilter,
    MetricResolver,
    MetricStatus,
    TimeGrain,
    build_catalog,
    compile_metric,
    definition_summary,
)
from libs.semantic.compiler import CompiledMetric
from libs.semantic.models import UnsupportedRequest

logger = logging.getLogger(__name__)

# Contracts change at human speed (a metric revision is a reviewed act), so a
# short TTL is plenty and keeps a newly approved version reaching every replica
# without a restart or a pub/sub channel.
_CACHE_TTL_S = 300
_cache: dict[str, tuple[float, MetricResolver]] = {}


def _row_to_definition(row: Any) -> MetricDefinition:
    """One joined metric+version row -> a validated MetricDefinition.

    Validation happens HERE, at load, not at compile: a malformed contract must
    be excluded from the catalog before it can ever emit SQL, and the operator
    who stored it should see it in the logs rather than a user seeing a failed
    question hours later.
    """
    try:
        aggregation = Aggregation(str(row.aggregation))
        grain = TimeGrain(str(row.default_grain or "none"))
        status = MetricStatus(str(row.status))
    except ValueError as e:
        raise InvalidMetricDefinition(f"metric {row.metric_key!r}: {e}") from e

    dimensions = tuple(
        MetricDimension.from_dict(d) for d in (row.dimensions or []) if isinstance(d, dict)
    )
    filters = tuple(
        MetricFilter.from_dict(f) for f in (row.default_filters or []) if isinstance(f, dict)
    )

    return MetricDefinition(
        key=str(row.metric_key),
        version=int(row.version),
        name=str(row.name or row.metric_key),
        aggregation=aggregation,
        source_table=str(row.source_table),
        expression=str(row.expression),
        time_column=str(row.time_column or ""),
        default_grain=grain,
        unit=str(row.unit or ""),
        dimensions=dimensions,
        default_filters=filters,
        status=status,
        owner=str(row.owner or ""),
        description=str(row.description or ""),
    )


_METRICS_SQL = """
SELECT m.metric_key, m.name, m.description, m.owner,
       v.version, v.status, v.source_table, v.aggregation, v.expression,
       v.time_column, v.default_grain, v.unit, v.dimensions, v.default_filters
FROM "SemanticMetric" m
JOIN "SemanticMetricVersion" v ON v.metric_id = m.id
WHERE v.status = 'approved'
"""

_SYNONYMS_SQL = """
SELECT m.metric_key, s.term
FROM "SemanticSynonym" s
JOIN "SemanticMetric" m ON m.id = s.metric_id
WHERE s.is_active = true
"""


async def _load_resolver(tenant_id: str) -> MetricResolver:
    """Read this tenant's approved metrics and synonyms into a resolver.

    Both queries are tenant-scoped by RLS rather than a WHERE clause — the
    session sets `app.tenant_id` (libs/database_connector get_client) and the
    `tenant_isolation` policy does the filtering, so forgetting a predicate here
    cannot leak another tenant's warehouse structure.
    """
    from sqlalchemy import text

    from libs.database_connector.postgres_connector import client

    if not client.is_connected():
        return MetricResolver(build_catalog([], {}))

    async with client.get_client(tenant_id=tenant_id) as session:
        metric_rows = (await session.execute(text(_METRICS_SQL))).fetchall()
        synonym_rows = (await session.execute(text(_SYNONYMS_SQL))).fetchall()

    definitions: list[MetricDefinition] = []
    for row in metric_rows:
        try:
            definitions.append(_row_to_definition(row))
        except InvalidMetricDefinition as e:
            # Skip the bad contract, keep the rest — one malformed metric must
            # not take the whole registry offline for the tenant.
            logger.warning("semantic | skipping invalid metric contract: %s", e)

    synonyms: dict[str, list[str]] = {}
    for row in synonym_rows:
        synonyms.setdefault(str(row.metric_key), []).append(str(row.term))

    logger.info("semantic | tenant=%s loaded %d metric(s)", tenant_id, len(definitions))
    return MetricResolver(build_catalog(definitions, synonyms))


async def get_resolver(tenant_id: str) -> MetricResolver:
    cached = _cache.get(tenant_id)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        resolver = await _load_resolver(tenant_id)
    except Exception as e:
        # A registry outage must degrade to "no metric matched" — i.e. the
        # existing LLM path — not fail the turn.
        logger.warning("semantic | registry load failed tenant=%s: %s", tenant_id, e)
        return MetricResolver(build_catalog([], {}))
    _cache[tenant_id] = (now, resolver)
    return resolver


def invalidate(tenant_id: str | None = None) -> None:
    """Drop cached contracts — call after approving a new version."""
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)


class SemanticMatch:
    """A question that the registry can answer exactly."""

    __slots__ = ("compiled", "definition", "matched_term")

    def __init__(self, definition: MetricDefinition, compiled: CompiledMetric, matched_term: str) -> None:
        self.definition = definition
        self.compiled = compiled
        self.matched_term = matched_term

    @property
    def provenance(self) -> dict[str, Any]:
        """Deterministic description of how the number was produced.

        Built from the contract, never restated by the model — the point of the
        layer is that this cannot drift from the SQL that actually ran.
        """
        return {
            "metric": self.compiled.metric_ref,
            "matched_term": self.matched_term,
            "definition": definition_summary(self.definition),
            "aggregation": self.compiled.aggregation,
            "unit": self.compiled.unit,
            "grain": self.compiled.grain,
            "dimensions": list(self.compiled.dimensions),
            "filters": list(self.compiled.applied_filters),
            "business_date_column": self.compiled.time_column,
            "owner": self.definition.owner,
        }


def slots_to_compiler_args(
    definition: MetricDefinition, slots: AnalyticalSlots,
) -> tuple[list[str], TimeGrain | None, tuple[str, str] | None, list[MetricFilter]]:
    """Bind resolved slots to this metric's contract.

    The dimension keys a follow-up names ("miền") are business names; the
    contract maps them to physical columns. A key the contract does not define
    raises rather than being dropped — silently ignoring "chỉ lấy miền Bắc"
    would return the whole country under a question that asked for one region,
    which is a wrong answer wearing the shape of a right one.
    """
    dimensions = [d for d in slots.dimensions if d]

    filters: list[MetricFilter] = []
    for slot_filter in slots.filters:
        # Raises UnsupportedRequest for an unknown key — caller falls back.
        dimension = definition.dimension(slot_filter.dimension)
        filters.append(slot_filter.to_metric_filter(dimension.column))

    grain = None
    if slots.grain:
        try:
            grain = TimeGrain(slots.grain)
        except ValueError:
            grain = None

    return dimensions, grain, slots.time_range, filters


async def match_question(
    tenant_id: str,
    question: str,
    *,
    dialect: str = "postgres",
    dimensions: list[str] | None = None,
    grain: TimeGrain | None = None,
    time_range: tuple[str, str] | None = None,
    slots: AnalyticalSlots | None = None,
) -> SemanticMatch | None:
    """Resolve + compile, or None when the registry cannot answer exactly.

    None covers three distinct cases, all of which mean "fall back": no metric
    matched, the registry is unreachable, or the metric matched but cannot serve
    this shape of request (an unknown dimension, a time filter on a metric with
    no business date). The last one is deliberately a fallback rather than an
    error — the general path may still answer it, just without the guarantee.
    """
    if not question:
        return None

    resolver = await get_resolver(tenant_id)
    # Slots carry the metric the planner resolved across turns, so a follow-up
    # that never repeats the word ("chỉ lấy miền Bắc") still reaches the right
    # contract. Falling back to the raw question keeps turn one working.
    resolution = None
    if slots is not None and slots.metric:
        resolution = resolver.resolve(slots.metric)
    if resolution is None:
        resolution = resolver.resolve(question)
    if resolution is None:
        return None

    extra_filters: list[MetricFilter] = []
    if slots is not None:
        try:
            dimensions, grain, time_range, extra_filters = slots_to_compiler_args(
                resolution.definition, slots,
            )
        except UnsupportedRequest as e:
            # A named dimension the contract does not define. Falling back is
            # right — but silently applying the rest and dropping that filter
            # would answer a different question than the one asked.
            logger.info(
                "semantic | metric %s cannot serve these slots (%s) — falling back",
                resolution.definition.ref, e,
            )
            return None

    try:
        compiled = compile_metric(
            resolution.definition,
            dialect=dialect,
            dimensions=dimensions,
            extra_filters=extra_filters,
            grain=grain,
            time_range=time_range,
        )
    except Exception as e:
        logger.info(
            "semantic | metric %s matched but cannot serve this request (%s) — falling back",
            resolution.definition.ref, e,
        )
        return None

    logger.info(
        "semantic | question resolved to %s via %r", resolution.definition.ref, resolution.matched_term,
    )
    return SemanticMatch(resolution.definition, compiled, resolution.matched_term)


async def check_underspecified(
    tenant_id: str, question: str, analysis_type: str, slots: AnalyticalSlots,
) -> list[dict[str, Any]]:
    """Structural gaps worth asking about BEFORE running anything.

    Deterministic complement to the orchestrator's LLM clarification step, which
    catches vague phrasing and misses the two cases that read as perfectly clear
    while silently producing the wrong number: no time range on a time-bound
    metric, and a metric name that covers two different formulas.

    Returns [] whenever nothing structural is missing, the registry is
    unreachable, or no metric resolved — an unrecognised question is the general
    path's business, not something to interrupt over.
    """
    if not tenant_id or not question:
        return []

    try:
        resolver = await get_resolver(tenant_id)
        resolution = None
        if slots.metric:
            resolution = resolver.resolve(slots.metric)
        if resolution is None:
            resolution = resolver.resolve(question)
        if resolution is None:
            return []

        alternatives = tuple(resolver.more_specific_than(resolution.matched_term))
        questions = ambiguity.check(
            analysis_type=analysis_type,
            slots=slots,
            definition=resolution.definition,
            metric_alternatives=alternatives,
        )
    except Exception as e:
        # A registry hiccup must not turn into a clarification prompt — that
        # would ask the user to resolve an outage they cannot see.
        logger.warning("semantic | underspecification check failed tenant=%s: %s", tenant_id, e)
        return []

    if questions:
        logger.info(
            "semantic | %d clarification(s) for %s: %s",
            len(questions), resolution.definition.ref, [q.reason.value for q in questions],
        )
    return [q.to_dict() for q in questions]
