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

__all__ = [
    "DEFAULT_LIMIT",
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
    "SemanticError",
    "TimeGrain",
    "UnsupportedRequest",
    "build_catalog",
    "compile_metric",
    "definition_summary",
    "normalize",
]
