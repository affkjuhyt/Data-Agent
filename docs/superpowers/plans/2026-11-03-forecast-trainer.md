# Forecast trainer, model creation, approval and real forecast pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A forecast model on real AdventureWorks data:
- defined in the UI;
- trained and backtested on statsforecast;
- written to the `forecast` schema;
- approved in the inbox;
- shown on the Dự báo and Mô hình pages from real data instead of mocks.

**Architecture:** This follows the python-sandbox pattern.
- `libs/forecast_fit` is a pure fitting library: series in, candidates, backtest and predictions out. It has no I/O.
- `ml-forecast` is a stateless HTTP service that runs that library. It has no database driver and no network client.
- `worker-agent` orchestrates:
  - it consumes `forecast.job.state_changed`;
  - it reads the series through the semantic compiler and executor as the metric owner, at the job's `cutoff_at`;
  - it calls `ml-forecast`, writes the version, backtest and predictions as `svc_forecast`, and submits the version;
  - it opens the inbox proposal.

  So the compiler is never duplicated, and the trainer never touches data it was not handed.

**Tech Stack:** Python 3.11; statsforecast (Apache-2.0); pandas and numpy; FastAPI for ml-forecast; SQLAlchemy async; PostgreSQL; React + vitest.

**Spec:** `docs/superpowers/specs/2026-11-03-forecasting-and-industry-documents-design.md` (G1, G2, G3 and G7's model seed; the "Trainer" and "Answers to the builder check" sections). This is plan 1 of 3. Plan 2 is crawl to documents. Plan 3 is Ask (both paths) together with the eval.

## Global Constraints

**Candidates and selection**
- Candidates are `naive`, `seasonal_naive`, `ets`, `theta` and `arima`, under the algorithm names the `model_version` CHECK accepts.
- No LightGBM.
- Selection: the lowest backtest MASE among candidates whose MASE < 1, measured against seasonal naive with m = 12 for month and m = 4 for quarter.
  - If none beats it, the job ends `failed` with the reason "Không mô hình nào tốt hơn cách đoán theo mùa (seasonal naive)", and nothing is submitted.
  - The two baselines can never be chosen, because MASE of seasonal naive against itself is 1.

**Backtest**
- Rolling origin, cutoffs ≥ 6 for month/quarter (table CHECK), horizon = the job's horizon.
- Metrics: MASE, sMAPE, coverage of 80% and 95%, and in-sample MASE.
- `model_version_guard` (forecast.sql 82–135) must accept the chosen version. The trainer never writes around it.

**Data and permissions**
- The training series must have n_points ≥ 24 (table CHECK).
  - With fewer points, the job fails with "Cần ít nhất 24 kỳ có dữ liệu; chuỗi này có N".
  - Periods with no row are filled with 0 only for additive (sum) metrics. Any other gap fails the job, naming the periods.
- Training identity is the metric owner (`context.metric.owner_id`), with the owner's row and column policies (invariant 13).
  - Each version records `scope_policy_hash`: a sha256 of the owner's sorted active policy ids and versions on the series' datasets, from `query/scope.py`.
  - It is stored in `model_card`.
- The series is read as of `cutoff_at` (BR-157).

**Intervals and labels**
- Intervals: statsforecast's own `level=[80, 95]` where the model gives them. `interval_method = "model"`, or `"conformal"` when computed from backtest residuals.
- Predictions of an unapproved version have `is_experimental = true`.

**Resources and licences**
- ml-forecast requests ≤ 256Mi memory and 100m CPU, has a limit of 1Gi, and runs one fit at a time.
- statsforecast and its dependencies must pass the repo licence rule (c10c3f57): no AGPL, ELv2 or non-commercial. Pin the versions.

**Language and repo workflow**
- Users see Vietnamese. Code, comments and test names are English.
- Schema changes go through tab3b, the generator and an idempotent alembic revision. Use the next free number on main at the time of the task (currently after v3_0027).
- API changes go through tab4, `build_a3` and `make api-types`.
- A new deployable needs all of: a directory under `backend/services/` with `__init__.py` and a Dockerfile, a k8s manifest in `deploy/k8s/dev/service/`, a network policy entry, and the Ansible/Makefile deploy lists.
  - Mirror how python-sandbox was added: find its commits with `git log --oneline -- backend/services/python_sandbox deploy/k8s` and copy every place it touched.
  - Do not write a new `.sh`.
- Tests run only through the make targets, never the host Python.
- `agentic/**` (data-agent-59) and `frontend/src/features/ask/**` (data-agent-20) are not touched by this plan.

## Review Focus

- **A series with a missing middle month on an average metric.** The job fails, naming the month. It is never silently filled with 0.
  - Owner: Task 3, `test_a_gap_in_a_non_additive_series_fails_the_job_naming_the_period`.
- **A flat or noisy series where nothing beats seasonal naive.** The job ends failed with the message, and no version or proposal is created.
  - Owner: Task 1, `test_nothing_beats_seasonal_naive_chooses_nothing`.
  - Owner: Task 3, `test_a_job_whose_candidates_lose_ends_failed_without_a_version`.
- **ml-forecast down or slow.** The job ends `failed` with "Dịch vụ dự báo không phản hồi". The worker is not blocked; there is a bounded request timeout.
  - Owner: Task 3, `test_the_trainer_down_fails_the_job_in_words`.
- **A non-admin/non-owner creating a model for a metric they cannot use.** Refused with 403, and no job is queued.
  - Owner: Task 4, `test_a_model_for_a_metric_the_caller_may_not_use_is_refused`.
- **Approving while the switch is off, by the builder who created it.** Refused (the existing `:approve`, BR-158). With the switch on, it is allowed and one `self_approval` row with step `forecast_model.decide` is written.
  - Owner: Task 5, `test_the_builder_approves_their_model_only_when_the_org_allows_it`.

---

### Task 1: `libs/forecast_fit`, the fitting library

**Files:**
- Create: `backend/libs/forecast_fit/__init__.py`, `backend/libs/forecast_fit/fit.py`
- Modify: `backend/services/worker_agent/requirements.txt` only if worker-agent runs the local fallback (see Task 2). The library's own requirement list goes in `backend/libs/forecast_fit/requirements.txt`, pinned.
- Test: `tests/unit/worker_agent/test_forecast_fit.py`, in the suite whose image installs statsforecast. If no current image does, add it to the test image (`tests/test-image.Dockerfile`) in this task.

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class Point: period: datetime.date; value: float
@dataclass(frozen=True)
class Forecast: period: datetime.date; point: float; lo80: float; hi80: float; lo95: float; hi95: float
@dataclass(frozen=True)
class Candidate: algorithm: str; mase: float; smape: float; coverage80: float; coverage95: float; in_sample_mase: float
@dataclass(frozen=True)
class FitResult:
    chosen: str | None              # None when no candidate has MASE < 1
    reason: str | None              # Vietnamese, set when chosen is None
    candidates: list[Candidate]     # every candidate including the baselines
    cutoffs: list[datetime.date]
    predictions: list[Forecast]     # of the chosen model, `horizon` periods after the last point
    interval_method: str            # "model" | "conformal"
    params: dict[str, Any]          # the chosen model's fitted parameters, JSON-safe
def fit(series: list[Point], *, frequency: Literal["month","quarter"], horizon: int, min_cutoffs: int = 6) -> FitResult
```
- Raises `ValueError` with a Vietnamese message for < 24 points or an unsorted or duplicate period.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_seasonal_series_chooses_a_model_that_beats_seasonal_naive`: 48 monthly points of trend + 12-month seasonality + seeded noise. Assert `chosen in {"ets","theta","arima"}`, its `mase < 1`, len(predictions) == horizon, `lo95 <= lo80 <= point <= hi80 <= hi95` for every prediction, and len(cutoffs) ≥ 6.
  - `test_nothing_beats_seasonal_naive_chooses_nothing`: pure seasonal repetition, where seasonal naive is perfect. Assert `chosen is None` and the reason starts with "Không mô hình nào tốt hơn".
  - `test_too_few_points_is_refused_in_words`: 20 points raises ValueError matching "ít nhất 24 kỳ".
  - `test_quarter_uses_season_length_four` (a 40-point quarterly series).
  - `test_the_result_is_deterministic`: the same input gives the same output twice.
- [ ] **Step 2: Run them and see them fail.** `make test-unit UNIT_SUITES=<suite with statsforecast> PYTEST_ARGS="tests/unit/worker_agent/test_forecast_fit.py -q"` → ImportError.
- [ ] **Step 3: Implement `fit`** with statsforecast's `StatsForecast` and `cross_validation` (rolling origin, `step_size=1`, `n_windows=max(min_cutoffs, …)` bounded by the data). The candidates are `Naive`, `SeasonalNaive(m)`, `AutoETS(m)`, `AutoTheta(m)` and `AutoARIMA(m)`.
  - MASE uses the in-sample seasonal-naive MAE as the scale.
  - Prediction intervals come from `level=[80, 95]`. If a model returns none, fall back to conformal intervals from that model's cross-validation residuals.
  - Use `n_jobs=1`.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit** `feat(forecast): a fitting library that backtests candidates against seasonal naive and keeps the best`.

### Task 2: `ml-forecast`, the stateless service, and its client

**Files:**
- Create:
  - `backend/services/ml_forecast/{__init__.py,main.py,Dockerfile,requirements.txt}`. FastAPI: `POST /fit` returns the FitResult as JSON; `GET /healthz`.
  - `backend/libs/forecast_fit/client.py`: `async def fit_remote(url: str, series, *, frequency, horizon) -> FitResult`, with a bounded timeout (60 s) and a capped, shape-checked body, as `libs/code_sandbox/client.py` does.
  - `backend/libs/forecast_fit/service.py` (optional): `async def fit_series(...) -> FitResult`. It uses `FORECAST_SERVICE_URL` when set, otherwise runs `fit` in-process in a thread. In-process is only for tests and local runs.
- Modify, every place python-sandbox's deploy touched: the k8s manifest and Service, `networkpolicy-service.yaml` (only worker-agent may reach it, and it has no egress), the Makefile deploy-local lists, the Ansible `k8s_common` service_workloads, and the worker-agent env (`FORECAST_SERVICE_URL`).
- Test:
  - `tests/unit/ml_forecast/test_main.py`: the FastAPI TestClient answers a seasonal series and refuses a malformed body with 422.
  - `tests/unit/repo/test_k8s_manifests.py`: extend it, following the python-sandbox cases.

**Interfaces:**
- Consumes `fit` (Task 1).
- Produces `fit_series(series: list[Point], *, frequency, horizon) -> FitResult`. Errors raise `TrainerUnavailable(str)` with a Vietnamese message: "Dịch vụ dự báo không phản hồi".

- [ ] **Step 1: Write the failing tests:**
  - the service round trip;
  - `test_a_malformed_answer_from_the_service_is_refused`, where the client gets a body of the wrong shape;
  - `test_the_service_timeout_raises_trainer_unavailable`, against a stub server that sleeps;
  - the manifest tests.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and see them pass.** Also run `make test-unit UNIT_SUITES=repo:api` to check the manifests.
- [ ] **Step 5: Commit** `feat(ml-forecast): a stateless trainer service the worker calls, with nowhere else to go`.

### Task 3: worker-agent trains a queued job

**Files:**
- Create: `backend/services/worker_agent/forecasting.py`, with `HANDLERS = {"forecast.job.state_changed": on_job}`.
- Modify: `backend/services/worker_agent/main.py`. Add `forecast` to the outbox sources with these handlers, as is done for `period` and `monitor`.
- Test: `tests/integration/worker_agent/test_forecasting.py`

**Interfaces:**
- Consumes `fit_series` and `TrainerUnavailable` (Task 2), the compiler and executor (`semantic/loader.py`, `semantic/compiler.py`, `query/executor.run_query`), and `query/scope.active_policies`.
- Produces:
  - `async def on_job(event: Event) -> None`. It acts only on `status == "queued"` and ignores everything else.
  - The job moves `queued → preparing → training → backtesting → ready` (or `failed`, with `error` in Vietnamese). Each change is its own `forecast.job.state_changed` event, emitted through `forecast.outbox` in the same transaction as the change.
  - On `chosen`:
    - write one `model_version`: `status='draft'`, the algorithm, params, training_from/to, n_points, `data_snapshot_refs = [query_run_id]`, and a `model_card` holding the backtest table, the limits text and `scope_policy_hash`;
    - write one `backtest_run` with the cutoffs, horizon, metrics (all candidates), baseline_metrics, mase and in_sample_mase;
    - write `horizon` rows of `prediction` with `is_experimental = true`;
    - then call the version's submit logic: the same rules as `POST …versions/{id}:submit`. Factor it out of `api/modules/forecast/models.py` into `libs` only if the import contract allows; otherwise duplicate nothing and emit `forecast.model_version.submitted` the way `:submit` does, setting `pending_approval`.
  - `job.model_version_id` is set.
  - The artifact: store the fitted params JSON in MinIO under `artifact_key = forecast/{org}/{version_id}.json`, through the worker's existing object-store client. If the worker has no such client, store params in `model_version.params` only and leave `artifact_key` NULL. Say which in the commit.

- [ ] **Step 1: Write the failing tests** (seed a metric, model and job on the test warehouse, following the `test_answers.py` bank fixtures; stub `fit_series` only for the failure cases):
  - `test_a_queued_job_trains_backtests_and_submits_a_version`. Asserts:
    - the job ends `ready`;
    - one draft→pending_approval version whose algorithm is in the candidates;
    - a backtest_run with ≥ 6 cutoffs;
    - `horizon` predictions with ordered intervals and `is_experimental`;
    - one `forecast.model_version.submitted` event;
    - `model_card.scope_policy_hash` is present.
  - `test_the_series_is_read_as_the_metric_owner_at_the_cutoff`: rows with `available_at` after `cutoff_at` are not in the training range.
  - `test_a_job_whose_candidates_lose_ends_failed_without_a_version`.
  - `test_a_gap_in_a_non_additive_series_fails_the_job_naming_the_period`.
  - `test_too_short_a_series_fails_in_words`.
  - `test_the_trainer_down_fails_the_job_in_words`.
  - `test_a_job_already_past_queued_is_ignored`: idempotent on a replayed event.
- [ ] **Step 2: Run them and see them fail.** `make test-integration INTEGRATION_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/integration/worker_agent/test_forecasting.py -q"`
- [ ] **Step 3: Implement.** Build the series request through the compiler: the metric, the model's scope as filters, `group_by` = the time dimension at the model's frequency, and the period from the first data period to the cutoff. Run it with the executor as the owner. Map the rows to `Point` values in period order.
- [ ] **Step 4: Run them and see them pass,** then run the whole worker_agent integration suite.
- [ ] **Step 5: Commit** `feat(forecast): worker-agent trains a queued job on the metric's own series and submits the best version`.

### Task 4: Define a model: API, A3 and the first job

**Files:**
- Modify: `backend/services/api/modules/forecast/models.py` (or a new `definitions.py`, following the module layout) and `schemas.py`.
- Add:
  - `POST /v1/forecast-models` with body `{metric_code, scope: {dimension_code: [member...]}, frequency: "month"|"quarter", horizon: 1..24}`.
    - It checks the caller holds `prepare_data` or `steward` in the space (follow how `:retrain` checks) and may use the metric: certified, in force, not blocked by policies for the caller.
    - It checks every scope dimension is allowed for the metric.
    - It creates the `forecast_model` (`owner_id` = the metric's owner, `status='active'`) and queues its first job (`progress.purpose='forecast'`, `cutoff_at = now`) through the existing queue helper in `jobs.py`.
    - It returns 201 with the model and the job id.
  - `GET /v1/forecast-jobs/{id}/workbench`: the job, its versions and backtests, shaped as `frontend/src/lib/v1/forecast.ts:370` reads it. Read that function and match it.
- Modify: `catalogue/source/tab4.md`, then `build_a3.py`, then `make api-types`.
- Test: `tests/integration/api/test_forecast.py` (extend).

- [ ] **Step 1: Write the failing tests:**
  - `test_defining_a_model_queues_its_first_job` (201; one job, queued; one event);
  - `test_a_model_for_a_metric_the_caller_may_not_use_is_refused` (403, nothing written);
  - `test_a_scope_on_a_cut_the_metric_does_not_allow_is_refused` (422 naming the cut);
  - `test_the_same_series_twice_is_a_conflict` (409; reuse the table's unique key if it has one, otherwise check it in the code);
  - `test_the_workbench_shows_the_jobs_versions_and_backtests`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement,** then regenerate the contract and the api types.
- [ ] **Step 4: Run them and see them pass,** together with `make test-unit UNIT_SUITES=api:api` (the catalogue gates).
- [ ] **Step 5: Commit** `feat(forecast): a model is defined on a certified metric and its first job queued`.

### Task 5: The submitted version reaches the inbox, reads in words, and is approved

**Files:**
- Modify:
  - `backend/services/worker_agent/forecasting.py`: on `forecast.model_version.submitted`, open an inbox proposal with `kind='forecast_model'`, `target_ref` = the version id, the title "Công bố mô hình dự báo …", and `assignee_id` = the metric owner. Use the same `_open` pattern as `semantic_review.py`.
    - The diff holds: metric code and name, scope, frequency, algorithm, mase, smape, coverage80/95, cutoffs count, training range, horizon, and the first 3 predictions.
  - The proposal's apply on accept, in the worker's proposals apply path: it calls the version's approve rules, as `POST …versions/{id}:approve` does. The API's `:approve` stays the one place that publishes. If apply must go through the API rules, move those rules into a function both call. With self-approval, the `self_approval` row with step `forecast_model.decide` comes from the inbox decide path (`proposal.review`) and the existing `:approve` check.
  - `backend/services/api/modules/inbox/describe.py`: the `forecast_model` kind.
    - Headline: "Công bố mô hình dự báo {Tên chỉ số} · {phạm vi} theo {tháng|quý}, {thuật toán}."
    - Fields: Thuật toán, Sai số backtest (MASE, against seasonal naive), Số điểm cắt, Độ phủ khoảng 80%/95%, Dữ liệu huấn luyện (from–to), Tầm dự báo.
    - checks_note: "Tốt hơn cách đoán theo mùa {x}%." This is (1 − MASE), formatted vi.
- Test:
  - `tests/integration/worker_agent/test_forecasting.py`: `test_a_submitted_version_opens_a_proposal_for_the_owner`.
  - `tests/integration/api/test_inbox_eval.py`: `test_a_forecast_model_proposal_reads_in_words`.
  - The API forecast tests:
    - `test_accepting_the_proposal_publishes_the_version` (the version is published, the model's current_version_id is set, and its predictions lose `is_experimental`);
    - `test_the_builder_approves_their_model_only_when_the_org_allows_it`.

- [ ] **Step 1:** Write the failing tests.
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them and see them pass, then run the api and worker_agent integration suites.
- [ ] **Step 5:** Commit `feat(forecast): a trained version is proposed to the metric owner in words and published on approval`.

### Task 6: The forecast pages on real data, and the create-model screen

**Files:**
- Modify: `frontend/src/lib/v1/forecast.ts`.
  - Remove the mock fallbacks for ForecastPage, ForecastModelPage and ForecastJobPage's calls, and use the real endpoints: `/v1/forecast-models`, `/{id}`, `/{id}/predictions`, `/{id}/accuracy`, versions, backtest, `/v1/forecasts/{id}` and `/events`, and the workbench.
  - Keep the mocks only in `frontend/src/mocks` for MSW tests.
- Create: `frontend/src/features/forecast/CreateForecastModelPage.tsx`, routed under Dự báo as "Tạo mô hình dự báo". It has:
  - a metric picker over the space's certified metrics, using the existing semantics list call;
  - a scope editor: for each allowed dimension, members from the value index;
  - frequency (Tháng/Quý), defaulting to Tháng;
  - horizon, from 1 to 24 and defaulting to 12.

  On submit it calls `POST /v1/forecast-models` and navigates to the job page, which follows the SSE progress already built.
- Modify: ForecastModelPage, ForecastPage and ForecastJobPage, so that an empty state shows "Chưa có mô hình dự báo" with a button to the create page.
- Test: vitest for the create page (validation, submit, navigation) and for each page rendering real-shaped API data, through MSW handlers updated to the real shapes. Run `make frontend-lint` and `make frontend-test`.

- [ ] **Step 1:** Write the failing tests.
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them and see them pass.
- [ ] **Step 5:** Commit `feat(forecast-ui): define a model, follow its training, read its forecast and backtest, on real data`.

### Task 7: The DS-AW seed, docs, backlog and the end-to-end path

**Files:**
- Modify the DS-AW seed under `deploy/docker/sql`, wherever the AdventureWorks metrics are seeded. Add two `forecast_model` rows with no version:
  - DOANH_THU, monthly, no scope;
  - DOANH_THU scoped to the product category Bikes, using the category dimension's code and member exactly as `ds-aw.yaml` names them.

  Both have `owner_id` = the metric owner and horizon 12. A comment says they are untrained definitions.
- Create: `tests/e2e/test_forecast_e2e.py` (`pytest.mark.e2e`). On the deployed cluster:
  1. define the company model through the API, or use the seeded one;
  2. retrain;
  3. wait for `ready`;
  4. accept the proposal as `helios-engineer` (self-approval on in DS-AW);
  5. read the predictions: 12, with ordered intervals and `is_experimental` false.
- Docs and backlog:
  - `docs/RUNBOOK.md`: how a forecast is trained, where the job and its error are, and how to approve it.
  - `docs/v3/04-backlog.md`: one item, marked done in the last commit.
  - `docs/v3/09-target-deploy-and-cleanup.md`: ml-forecast now exists.
  - `docs/v3/checklist/status.tsv`: "ml-forecast chưa có" changes.
  - Run `make design-checklist`.
- Verification:
  - `make lint` (0 added)
  - `make type-v3` (no new errors)
  - `make lint-imports`
  - `make frontend-lint`
  - `make frontend-test`
  - `make test-unit`
  - `make test-integration`
  - `make design-checklist`

- [ ] **Step 1:** Write the e2e test and the seed.
- [ ] **Step 2:** Run the integration suites, which must stay green with the seed loaded.
- [ ] **Step 3:** Write the docs and backlog, then regenerate the checklist.
- [ ] **Step 4:** Run the full verification list.
- [ ] **Step 5:** Commit `feat(forecast): DS-AW ships two untrained forecast definitions; the e2e path trains, approves and reads one`.
