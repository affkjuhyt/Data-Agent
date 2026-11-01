"""Pick a chart type from the shape of a result cut (AN-03 precursor).

The Ask FE draws eight Recharts kinds (`ChartRenderer.tsx`). Until this module existed,
`chart_spec_of` only chose bar vs line. Intent classes mirror common BI taxonomies
(compare / time / part-to-whole / …) so a later validator can reason about whether the
chosen type fits the measure, without an LLM and without inventing values.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

# Keep in sync with frontend/src/features/ask/types.ts ChartSpec.chart_type.
ChartType = Literal["bar", "line", "area", "pie", "scatter", "radar", "radialBar", "composed"]

# Pie stays readable only with a short category list; beyond this, bars compare better.
MAX_PIE_MEMBERS = 8

PART_TO_WHOLE_COLUMNS = frozenset({"share", "cumulative"})


class Intent(str, Enum):
    COMPARING = "comparing"
    TIME = "time"
    DISTRIBUTION = "distribution"
    PART_TO_WHOLE = "part_to_whole"
    RELATIONSHIP = "relationship"
    GEOSPATIAL = "geospatial"


def classify(
    *,
    group_by: list[str],
    time_dimensions: set[str],
    value_column: str = "value",
) -> Intent:
    """The analysis goal implied by the cut and the column being drawn."""
    if not group_by:
        return Intent.COMPARING
    x = group_by[0]
    if x in time_dimensions:
        return Intent.TIME
    if value_column in PART_TO_WHOLE_COLUMNS and value_column == "share":
        return Intent.PART_TO_WHOLE
    return Intent.COMPARING


def recommend_type(
    intent: Intent,
    *,
    n_members: int,
    has_series: bool = False,
    value_column: str = "value",
) -> ChartType:
    """Map intent + cardinality to one of the eight FE chart types."""
    if intent == Intent.TIME:
        if value_column == "cumulative":
            return "area"
        return "line"
    if intent == Intent.PART_TO_WHOLE:
        if not has_series and 2 <= n_members <= MAX_PIE_MEMBERS:
            return "pie"
        return "bar"
    if intent == Intent.DISTRIBUTION:
        return "bar"
    if intent == Intent.RELATIONSHIP:
        return "scatter"
    if intent == Intent.GEOSPATIAL:
        return "bar"
    return "bar"
