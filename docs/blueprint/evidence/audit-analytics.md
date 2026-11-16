# ANALYTICS domain audit (read-only, 2026-11-12, main @ 25b5482e)

Paths are under `/Users/thiennlinh/Dev/Projects/Data-Agent/`. WA = `backend/services/worker_agent`.

## 1. Analysis kinds end-to-end

| Kind | Verdict | Where / how | Deterministic or LLM |
|---|---|---|---|
| Basic aggregation | exists | semantic compiler → `WA/query/executor.py` (`run_query` L388, `MAX_ROWS = 10_000` L78); figures F1..Fn `WA/answer/narrate.py:94 figures_of` | deterministic SQL; text template or LLM narrator over `{{F}}` tags |
| Comparison (compare_to, PoP) | exists | `WA/semantic/compare.py:1-12` (UNION ALL + GROUP BY, two sides from `compile_metric`); fast path `WA/answer/comparison.py` (change per row as derived figure, pp for %; L1-12); agentic `tools/query.py:62,187` `compare_to: previous_period \| same_period_last_year` | deterministic |
| Trend (slope, streaks) | exists (fast path only) | `WA/answer/insights.py:265 _trend` (least-squares slope as linear expr over figures), `:289 _streak`, `:199 _over_time`; gated `MIN_FOR_TREND=4` | deterministic; agentic path has no trend primitive (reporter told "số chưa có thì không nêu", `agentic/prompts.py:170-172`) |
| EDA / distribution shape | partial | `insights.py:12-21` top-k concentration (additive only), break-vs-even (`BREAK_GAP/EVEN_GAP` L52-54), MAD outlier (`OUTLIER_Z=3.5`, L45); only single-dimension cuts (`insights_of` L171: `len(group_by)!=1 → Insights()`). No histogram, quantiles, correlation, 2-D cuts | deterministic |
| Root-cause / driver decomposition | exists, two routes | Skill: `WA/skills/decomposition.py:1-22` (additive and ratio `A/B*k`, rate/mix split, reconciliation `RECONCILE_TOLERANCE` L257, `method`+`confidence` L280-284, cut ranking by `concentration` L165); run via `WA/skill_runs.py` outbox handler (only step kind `decompose`, L148-152). Agentic: `agentic/tools/explain.py` (`ExplainChange`, hypothesis→experiment per cut, ranks by concentration L94-101, says "không phải nguyên nhân" L146) | deterministic arithmetic; LLM only chooses the tool |
| Anomaly detection | partial | `WA/skills/anomaly.py` (MAD z) has **only test callers** (`tests/unit/worker_agent/test_anomaly_skill.py`) – not wired to any runtime; `insights.py` outlier sentence is the only live path; `monitor_backtests.py:14` `seasonal_anomaly` → "unsupported" | deterministic, dead at runtime |
| Forecasting | exists | `WA/forecasting.py:1-33` → `ml_forecast/main.py` (stateless POST /fit) → `libs/forecast_fit/backtest.py:42-46` candidates naive, seasonal_naive, AutoETS, AutoTheta, AutoARIMA (statsforecast 2.1.1); intervals model-native or conformal fallback `:144-146`, `LEVELS`; MASE vs seasonal naive gate (BR-158/172 `:243-244`); versioning `forecast.model_version` + backtest_run; approval via inbox proposal `forecast.model_version.submitted` (forecasting.py L23-27); `tools/forecast.py` lands mean+intervals as `exploratory` dataset; ledger `api/modules/forecast/ledger.py` (rescore 501) | deterministic |
| What-if | partial | only formula Monte Carlo (`api/modules/forecast/formula.py`); `fc.Prediction.scenario_id` exists (`tools/forecast.py:331`) but Ask refuses: "mở studio để what-if" L69 | — |
| Cohort / funnel | missing / dead | `WA/skills/funnel.py` has test callers only; no cohort code anywhere (`grep -ri cohort WA` → none) | — |

`skills/report_outline.py` also has only test callers. `skills/__init__.py` promises "Deterministic analysis skills (UC-03)" but `skill_runs.unrunnable` rejects every category but `decomposition`.

## 2. Python sandbox

- Tool: `WA/agentic/tools/analyze.py`. `mode()` L14-23: `AGENT_PYTHON_SANDBOX=off|service|local`; service without URL → off (never silently local). Dev cluster sets `service` (`deploy/k8s/dev/service/worker-agent.yaml:31-32`).
- Service: `backend/services/python_sandbox/main.py`. Plain `ThreadingHTTPServer`, `POST /run`, `MAX_BODY 20 MiB`, `BoundedSemaphore(1)` (one run per pod, L24-26), body not logged L79.
- Pod: `deploy/k8s/dev/service/python-sandbox.yaml` `automountServiceAccountToken:false` L11/37, `readOnlyRootFilesystem` L61, `runAsNonRoot` L69, `seccomp RuntimeDefault` L72, `/tmp` memory emptyDir 256Mi L67, limits cpu 2 / mem 3Gi L57. NetworkPolicy `deploy/k8s/dev/networkpolicy-service.yaml:24-49`: ingress only from `worker-agent:8140`, `egress: []`.
- Inner layers `libs/code_sandbox/local.py`: AST check `check()` L168-197 (allowlist L33-44: pandas, numpy, math, statistics, datetime, json, re, collections, itertools, functools, decimal; `WHOLE_ONLY` pandas/numpy; forbidden names/attrs/prefixes L45-144, any `__` string constant rejected L197); child interpreter with no env, `RLIMIT_CPU 15 s`, `RLIMIT_AS 1 GiB` L146-148/332-335, `unshare -rn` when available L342-367, `sys.addaudithook` guard L228-282, `TIMEOUT_S 20`, `MAX_RESULT_ROWS 2000`, `MAX_STDOUT 4000`.
- Client `libs/code_sandbox/client.py`: re-runs `check` before HTTP, caps response 8 MiB, `MAX_COLUMNS 200`, shape-validates scalars only L24-54.
- Data in/out: JSON `{columns, rows}` of workspace datasets (not a result-set id; the whole dataset, up to the executor's 10 000 rows, is re-serialised per call, no input size cap other than 20 MiB body). Out: new `Dataset(trust="derived", code=args.code, query_run_ids=inputs' runs)` `analyze.py:80-92`.
- Invariant check: the model writes code, the sandbox computes; output figures become facts the reporter cites by tag (`agentic/verify.py:1-5`). Consistent with "LLM never states a number", but the **computation itself is model-authored and unchecked** (no semantic check that `growth = (a-b)/b`, no unit propagation beyond "all inputs share one unit" L83).
- Tests: `tests/unit/worker_agent/test_agentic_core.py:135-170` (refuses files/network/dunders; runs pandas; error path). No test of the service pod, NetworkPolicy or `client._shaped`.

## 3. Numeric correctness controls

- Figures/tags: `answer/narrate.py` (fast path template), `answer/narrator.py:24-30` SYSTEM forbids digits outside `{{F}}`, post-check drops stray sentences; agentic equivalent `agentic/verify.py` (`MAX_FACTS 400`, `ROWS_PER_DATASET 40`). `insights.py` derived figures carry `expr`; receipt stores `source_kind=compute_run`, `expr_sha256` (`WA/questions.py:2585-2598`).
- Replay: `WA/replay.py:1-17` re-runs queries, compares SQL sha / result sha / values, re-evaluates `expr` for compute figures. Integration test `tests/integration/worker_agent/test_answers.py:1383`. **Fast path only**: the agentic `persist` (`agentic/engine.py:1161-1300`) writes `qa.Answer(figures, blocks)` and no `AnswerReceipt` (only writer is `questions.py:2531`); agentic figure `source = {query_run_id: runs[0]}` (`engine.py:~1080`), so a sandbox-derived figure points at its first input run, not at code+inputs.
- `causal_claim_rate`: `model/eval/scorers/causal.py:31-36`, regex over accent-stripped text; runs REAL skill functions offline on 12 cases (`model/eval/cases/why.jsonl`), not model output. Also `why_method_coverage`, `why_decomposition_accuracy`. Wired in `model/eval/run.py:36`.
- Additivity: `WA/semantic/analysis.py:51-59 additive_measures` (declaration only narrows; distinct count never additive), used by share/cumulative/insights/decomposition. Ratio-of-averages: `insights.py:14-16` no total for ratio; `decomposition.py` refuses non-additive/non-ratio shapes. COUNT DISTINCT share: refused at compile (`analysis.py:19-21`); eval case `expect.refused` in why.jsonl. AN-03 **chart validator does not exist**: only `CHART_ROLES` fit (`agentic/blocks.py:46-53`, `chart_fits` L86) — x-kind, measure count, row count, non-negative for pie. No check for constant measure, duplicate-meaning columns, grain mismatch, share on non-additive measure, hidden axis labels; no Review Inbox proposal. Backlog AN-03 `todo` (`docs/v3/04-backlog.md:159`).

## 4. Visualization

- Fast path: `answer/narrate.py:136 chart_spec_of` → `answer/chart_intent.py` picks only `area|line|pie|bar` (L59-66) though `ChartType` lists 8; intent recorded in spec.
- Agentic: `agentic/blocks.py` block types `markdown,kpi,chart,table,insights,callout,code,follow_ups` L27; `CHART_ROLES` has 6 types (no radar/radialBar); server swaps to first fitting type L275.
- FE: `frontend/src/features/ask/types.ts:19` 8 Recharts kinds in `ChartRenderer.tsx`; `dataAnswer.ts:151-154 readChartSpec` validates only shape + `CHART_WIRE_TYPES`.
- Dashboards/tiles: `WA/tiles.py` (sql/table/question/chart tiles → `tile_snapshot`, shape only, values via `/v1/query-runs/{id}/rows`), api `workspace/routes.py:605 refresh_workspace`, `:1225 list_tile_snapshots`. Reports: `WA/report_runs.py` renders **xlsx only**; "pdf rendering is not made (`not_supported`)" L20 while the api default format is `_pdf()` (`monitor/schemas.py:142`). Exports: `WA/exports.py` (UC-01-06, HITL on detail rows, formula-injection guard, 24 h expiry).

## 5. Reproducibility

- Query runs: `executor.py` records `result_hash` sha256 of columns+rows (L220, L495-631), compiled SQL sha; receipt `qa.AnswerReceipt` + `AnswerReceiptFigure` sealed in the answer transaction (`questions.py:2516-2600`) → the "receipt" is `GET /v1/.../receipt`, FE `AnswerReceiptPage` (`routes.tsx:190`).
- Not covered: agentic answers (no receipt, see §3); sandbox runs (code stored only as `Dataset.code` in the answer's blocks for display, `workspace.py:71-72`; no `compute_run` row, no input dataset hashes, no pandas/numpy version pin recorded); tiles store shape only; replay has no time travel (`replay.py:9-12`); `dataset_version` not referenced by receipt (forecast ledger notes rescore against corrected version is "not yet", `ledger.py`). Chart re-run: a tile chart re-runs its SQL (`tiles.py`), an Ask chart cannot be re-run except through full receipt replay (fast path).

## 6. Insight generation

- Fast path: `insights.py` sentences passed as `nhan_dinh` hints to narrator (`narrator.py:27-28`: "Không nêu nguyên nhân khi không được cho"). Thresholds hint `answer/thresholds.py`.
- Agentic: `agentic/prompts.py:158` insights block = 2-5 LLM-written sentences with tone; L176 "Không suy đoán nguyên nhân"; judge L212-213 flags causal/trend claims without a proving step. `follow_ups` (L161-164) = "Nên xem tiếp": LLM-written, constrained to seen metrics/dimensions, **not executable queries** (plain text). Hypothesis block (AN-06): missing — no `hypothesis` block type, no test catalogue; `ExplainChange` is the only hypothesis-shaped thing. Backlog AN-06 `todo`.
- No scorer runs `causal_claim_rate` over actual narrator/reporter output (eval is on skill output).

## 7. DuckDB / Polars / Arrow

Absent. Only `pyarrow` as a transitive pin in `libs/forecast_fit/requirements.txt` (statsforecast). Result sets are `list[list[Any]]` JSON rows (`executor.py:190`), cap 10 000 rows (BR-03); workspace datasets are the same lists; sandbox output cap 2 000 rows; chart/table blocks `MAX_BLOCK_ROWS 200` (`workspace.py:26`), preview 12 rows. pandas only inside the sandbox child.

## 8. Monitors / alerts / digests

- Schedules: Temporal Schedules (`WA/schedules/targets.py` docstring L1-16): ingestion, pipelines, report schedules (→ `report_runs.py`, digests `digests.py` kind `digest`), hourly quality pass, crawl. **Monitor evaluation is not scheduled**: `targets.py` touches `monitor.ReportSchedule` only (L140, L353); no code writes `monitor.alert` (only `deploy/docker/sql/v3-screen-audit-seed.sql:203`). Alerts shown in `WatchListPage`/`AlertPage` come from seed.
- Detection logic exists only as backtest: `WA/monitor_backtests.py` threshold (`answer/thresholds.py`), `change_vs_previous` %, `seasonal_anomaly` → unsupported. Rules `api/modules/monitor/rules.py` (noise warning 30 %, BR-124 close note).
- Alert → investigation: FE `AlertPage.tsx:107-108` → `investigateAlert` (`lib/v1/alerts.ts:134-144`) = `POST /v1/workspaces` with title/description; "WorkspaceCreate.created_from has no 'alert' yet" (L135) — no server-side link, no auto-run `ExplainChange`.
- Digest: template text, metric month vs previous month, alerts of last week (`digests.py:1-13`).

## 9. Tests

Unit (`tests/unit/worker_agent`): `test_insights.py`, `test_chart_intent.py`, `test_chart_spec.py`, `test_comparison_answer.py`, `test_compare.py`, `test_thresholds.py`, `test_decomposition.py`, `test_anomaly_skill.py`, `test_funnel_skill.py`, `test_report_outline_skill.py`, `test_analysis.py` (additivity), `test_narrator.py`, `test_present_gate.py`, `test_agentic_core.py` (sandbox check/run, plan DAG), `test_forecast_metric_tool.py`, `test_monitor_backtests.py`, `test_skill_evals.py`; `tests/unit/ml_forecast/test_main.py` (HTTP contract). Integration: `test_answers.py` (receipt/replay incl. derived figures L1383), `test_comparisons.py`, `test_adventureworks_analysis.py` (share/cumulative reconciled against hand SQL), `test_forecasting.py` (fit **stubbed**), `test_skill_runs.py`, `test_tiles.py`, `test_report_runs.py`, `test_digest_waiting.py`, `test_exports.py`, `test_monitor_backtests.py`. Real model: none in pytest; only `make agent-eval` (`agentic/evaluate.py`) and `make sqlgen-ablation` against the served model, and `tests/e2e/test_forecast_e2e.py` (deployed cluster). `model/eval` why-scorer is model-free.

## 10. Top 10 gaps vs real analytical value

| # | Gap | Sev | Fix lands in |
|---|---|---|---|
| 1 | Monitors never evaluate on schedule; no alert is ever produced at runtime | High | `WA/schedules/targets.py` (+ a `monitor_runs.py` reusing `monitor_backtests` judge) |
| 2 | Agentic answers have no sealed receipt and no replay; derived (sandbox) figures trace to an input run, not to code+inputs | High | `WA/agentic/engine.py:1161 persist` → call `questions._seal_receipt`; `replay.py` step kind for sandbox |
| 3 | Sandbox computation is validated only for safety, not for meaning (no unit/additivity propagation, no check that a "share" came from additive inputs) | High | `WA/agentic/tools/analyze.py` + `semantic/analysis.additive_measures` |
| 4 | AN-03 chart validator absent (constant measure, grain mismatch, share on non-additive, duplicate columns) | High | new `WA/agentic/chart_check.py` called from `blocks.materialize` and `answer/narrate.chart_spec_of` |
| 5 | Trend/EDA primitives exist only on the single-metric fast path; the agent must hand-write pandas for slope/streak/outlier | Med | `WA/agentic/tools/` deterministic `insights` tool wrapping `answer/insights.py`, `skills/anomaly.py` |
| 6 | `anomaly`, `funnel`, `report_outline` skills are dead code; `skill_runs` runs only `decompose` | Med | `WA/skill_runs.py:148 unrunnable` + step kinds |
| 7 | Alert → investigation is a blank workspace; no context, no auto `ExplainChange` | Med | `api/modules/workspace` `created_from=alert`; `WA/agentic` seeded plan |
| 8 | Hypothesis block / executable "Nên xem tiếp" missing; follow-ups are free text | Med | `agentic/blocks.py` new block + `prompts.py`; FE `ComposedBlocks.tsx` |
| 9 | `causal_claim_rate` never measured on real narrator/reporter text; 12 why cases; no real-model gate in CI | Med | `model/eval/run.py` + `WA/agentic/evaluate.py` export text to scorer |
| 10 | Reports: PDF default but not rendered; tiles snapshot shape only; no DuckDB/Arrow so every analysis re-ships 10 k-row JSON to the sandbox | Low–Med | `WA/report_runs.py`, `WA/tiles.py`, `libs/code_sandbox/client.py` |
