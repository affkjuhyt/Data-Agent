"""Semantic Layer — business meaning as versioned, owned contracts instead of prose.

Two halves, deliberately separate:

  * `resolver` decides WHICH metric a question is about (deterministic synonym
    matching, longest phrase wins);
  * `compiler` decides HOW that metric is computed (aggregation, expression,
    default filters and business date come from an approved contract, not from
    a model).

Pure — no database driver, no network, no service imports — so `libs/` stays a
leaf layer under .importlinter. Storage and caching live in the service that
uses it (services/workflow-runtime/semantic_layer.py).
"""
from libs.semantic.compiler import (
    DEFAULT_LIMIT,
    CompiledMetric,
    compile_metric,
    definition_summary,
)
from libs.semantic.models import (
    Aggregation,
    FilterOp,
    InvalidMetricDefinition,
    MetricCatalog,
    MetricDefinition,
    MetricDimension,
    MetricFilter,
    MetricNotFound,
    MetricStatus,
    SemanticError,
    TimeGrain,
    UnsupportedRequest,
)
from libs.semantic.resolver import MetricResolver, Resolution, build_catalog, normalize
from libs.semantic.slots import (
    COMPARE_PREVIOUS_PERIOD,
    COMPARE_SAME_PERIOD_LAST_YEAR,
    COMPARISONS,
    RELATIVE_PERIODS,
    AnalyticalSlots,
    SlotFilter,
    merge,
)

__all__ = [
    "COMPARE_PREVIOUS_PERIOD",
    "COMPARE_SAME_PERIOD_LAST_YEAR",
    "COMPARISONS",
    "DEFAULT_LIMIT",
    "RELATIVE_PERIODS",
    "AnalyticalSlots",
    "Aggregation",
    "CompiledMetric",
    "FilterOp",
    "InvalidMetricDefinition",
    "MetricCatalog",
    "MetricDefinition",
    "MetricDimension",
    "MetricFilter",
    "MetricNotFound",
    "MetricResolver",
    "MetricStatus",
    "Resolution",
    "SlotFilter",
    "SemanticError",
    "TimeGrain",
    "UnsupportedRequest",
    "build_catalog",
    "compile_metric",
    "merge",
    "definition_summary",
    "normalize",
]
