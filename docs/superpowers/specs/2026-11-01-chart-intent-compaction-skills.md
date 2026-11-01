# Chart intent, compaction, deep-analysis skills

## Intent

Borrow patterns from Zafer-Liu/Data-Analysis-Agent (ideas only; CC BY-NC — no code copy) to improve Ask answers without weakening V3 invariants (permissions, semantic layer, BR-171 number provenance, trust labels).

## Goals

1. **Chart intent → type**: classify a result cut into COMPARE / TIME / DISTRIBUTION / PART_TO_WHOLE (and related) and pick among the eight FE chart types, not only bar/line.
2. **History compaction**: older Ask turns summarise metrics, dimensions, filters, periods, and numbers from the question text — never invent figures.
3. **Skill modules**: deterministic pure-Python skills (anomaly MAD, funnel conversion, report outline) in the same shape as `skills/decomposition.py`, ready for later `skill_runs` wiring.

## Non-goals

- 41 Plotly chart implementations
- LLM-based compaction
- Full AN-03 validator / Review Inbox proposals
- Wiring new skill kinds into `skill_runs.py` (still decompose-only)

## Constraints

- Existing bar/line behaviour for category/time cuts stays the default.
- Pie only when part-to-whole (share/cumulative column or additive share reading) and few members, no series.
- FE `readChartSpec` must accept the new types ChartRenderer already draws.
- Tests in `tests/unit/worker_agent` (+ FE vitest for chart wire).
