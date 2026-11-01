# Chart Intent, Compaction, Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recommend richer chart types from result shape, compact older Ask turns with preserved metrics/numbers, and add three deterministic analysis skill modules.

**Architecture:** Pure helpers under `worker_agent/answer` and `worker_agent/understand` plus `worker_agent/skills/*`; `chart_spec_of` and `history_of` call them. FE widens `readChartSpec` to match ChartRenderer.

**Tech Stack:** Python 3.11, pytest (worker-agent image), vitest for Ask FE.

**Spec:** `docs/superpowers/specs/2026-11-01-chart-intent-compaction-skills.md`

## Global Constraints

- No copied code from Zafer-Liu/Data-Analysis-Agent (CC BY-NC).
- BR-171 / figure provenance unchanged; compaction never invents numbers.
- Do not reformat unrelated files.

## Review Focus

- Category cut still yields `bar`; time cut still `line`.
- Share/part-to-whole with ≤8 members and no series → `pie`.
- Older-turn summary lists metrics, cuts, filters, periods, and numbers found in question text.
- Anomaly/funnel/report modules are pure and unit-tested.

### Task 1: Chart intent + chart_spec_of

- [x] Done — `chart_intent.py`, `chart_spec_of`, FE `readChartSpec`, unit + vitest green

### Task 2: Compaction summary for history_of

- [x] Done — `understand/compaction.py` + `history_of`; AC-01-04-05 tests green

### Task 3: Skill modules

- [x] Done — `anomaly`, `funnel`, `report_outline` library modules + unit tests (not yet wired in `skill_runs.py`)
