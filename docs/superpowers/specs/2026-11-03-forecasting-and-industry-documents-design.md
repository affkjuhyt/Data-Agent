# Forecasting on real data and an industry-outlook corpus, answered together in Ask

Date: 2026-11-03.

Status: agreed in chat by the user, who chose "cả hai" and, on the model, said "dựng 1 model được không, tôi muốn nó làm hoàn chỉnh ấy, không chắp vá như này". They also said "tự thu thập các trang web công khai cộng với cả tải lên" and, after reading the list of sources, "oke đồng ý".

Calls marked *(my call)* were not discussed and can be changed.

## Why

On DS-AW the user asked "tương lai ngành bán xe đạp như nào". Ask answered "Không tìm thấy trong tài liệu được phép…". That answer is correct by design: no metric was named, no document covers the topic, and the LLM may not invent an outlook (invariant 6). But it is useless.

The question holds two questions:

1. **Where the company's own sales are heading.** This is answerable from data with a forecast.
2. **Where the industry is heading.** This is answerable only from outside sources, which need a corpus.

The forecast module has only its frame:

- the `forecast` schema with its guards (publish only with a finished backtest, MASE < 1, and an overfit bound);
- the API to read, submit, approve and reject;
- the pages: Dự báo, Mô hình, Studio, Ledger.

**Nothing trains.** `backend/services/ml_forecast` (named in docs/v3/09) does not exist. No forecasting library is installed. Nothing consumes `forecast.job.state_changed`, so the pages run on mocks. No endpoint or screen creates a model for a series. A submitted version never reaches the inbox. Ask has no forecast tool, and the DS-AW eval case aw054 "Dự báo doanh thu năm 2015" expects a refusal.

For documents, the upload path and Ask's citations work. The crawler, though, produces structured records only: there is no path from a crawled web page to a document Ask can cite.

The user wants it complete, end to end. That means three things: a real model trained on AdventureWorks data, backtested, approved and served; Ask answering future questions from it; and an industry corpus built from crawled public pages plus uploads, cited in the same answer.

## Goals

- **G1. A trainer (`ml-forecast` service)** turns a queued forecast job into model versions:
  1. Read the metric series point-in-time through the semantic compiler and executor, never raw SQL.
  2. Fit candidates. The baselines are seasonal naive and naive; the candidates are ETS, Theta and ARIMA.
  3. Run a rolling-origin backtest.
  4. Pick the best by MASE against seasonal naive.
  5. Write the `model_version`, `backtest_run` and `prediction` rows (point, 80% and 95% intervals) and the MinIO artifact.
  6. Move the job through its states (queued → preparing → training → backtesting → ready or failed).
  7. Submit the chosen version for approval.
- **G2. Create a model from the UI.** A screen and endpoint define a series:
  - a certified metric (owner: the metric's owner);
  - a scope, as filters on allowed dimensions, for example product category = Bikes;
  - a frequency (month or quarter);
  - a horizon.

  Creating the model queues the first training job. "Huấn luyện lại" queues the next one.
- **G3. Approval in the inbox.** A submitted version opens a proposal of kind `forecast_model`, decided by the metric owner. With the switch on, the builder may approve it themselves (the self-approval plan, step `forecast_model.decide`). Approving publishes the version through the existing `:approve`.
  - The proposal reads in words, like every other kind (`inbox/describe.py`). For example: "Công bố mô hình dự báo Doanh thu · Xe đạp theo tháng, ETS, MASE 0,72 so với seasonal naive, backtest 8 điểm cắt."
  - It also shows the backtest table and the forecast chart.
- **G4. The industry corpus from the web.** A crawl source can be marked as documents. Each published crawl run turns its fetched pages into documents in a chosen collection through worker-document's existing extractor and chunker, so the pages become searchable and citable.
  - Each document keeps its source URL, the fetch time and the source's licence line.
  - Legal review (BR-174) gates every source, as it does today.
  - A source whose review lapses (184 days, or changed terms) stops adding documents.
  - What was already published stays, marked with its validity.
- **G5. The industry corpus from uploads.** The existing `POST /v1/documents` puts files into the same collection. No change, other than the collection the seed creates.
- **G6. Ask answers future questions** on both answer paths:
  - the dynamic agent, `agentic/`, owned by the other session data-agent-59;
  - the governed path, `questions.py`.

  Ask detects future intent ("tương lai", "xu hướng", "dự báo", "sắp tới", "năm sau", "triển vọng", "forecast", "outlook", "next year"…) and answers in up to three blocks:
  1. **Theo dữ liệu công ty: xu hướng.** The past periods with data: a chart, the change, and what drove it (the decomposition skill).
  2. **Theo dữ liệu công ty: dự báo.** Comes from the **published** model for that metric and scope: points with their 80% and 95% bands, the model and its backtest error in one line, and the horizon counted **from the last period with data**.
     - If no published model exists, the block says so and links "Tạo mô hình dự báo".
     - A draft or unapproved model is never shown as the answer.
  3. **Theo tài liệu ngành.** Cited passages from the industry collection, or "Chưa có tài liệu về triển vọng ngành" with links to upload a file or add a source.

  The blocks never mix numbers: no sentence relates a company figure to an industry figure, because the periods do not overlap (see constraints). A question about the industry only ("triển vọng ngành xe đạp châu Âu") gets block 3 only.
- **G7. The DS-AW dataset ships ready to show:**
  - two model definitions, company revenue and Bikes revenue, monthly, seeded as data (not trained versions; see the builder-check answers);
  - the "Tài liệu ngành" collection;
  - crawl sources for group A, with their legal review left for the user to do;
  - the eval set updated: aw054 expects the "chưa có mô hình dự báo đã duyệt" block on an untrained seed, and there are new cases for an industry question and a mixed question.

## Non-goals

- No LightGBM in this round. AdventureWorks has about 43 monthly points, and the table's CHECK requires at least 60 (or 36 with 5 pooled series). Scenarios, exogenous regressors and the fuel formula are untouched.
- No automatic web search at answer time. Ask cites only what is already in the collection.
- No forecasting by the LLM, and no numbers in prose that the database did not produce (invariant 6). The planner never chooses an algorithm. The trainer chooses, by backtest.
- No paywalled or trade-press sources (group C). There is no general crawler for arbitrary URLs. Each source is declared and reviewed.
- No retraining schedule beyond the existing `retrain_cron` column. A cron is honoured if set, but none is seeded.

## Anti-goals (failure even if it works)

- A forecast that reaches an answer without a published version, or without its backtest beating seasonal naive.
- A forecast presented as starting "now" (2026) when the data ends in 2014.
- An industry passage presented as data about AdventureWorks, or a company figure presented as the industry's.
- A page from a source without a valid legal review getting into the collection. Text from a group C site getting stored.
- A trainer that blocks the worker or exhausts the cluster. The local cluster has 6 GB and runs at about 98% of requests.

## Constraints

- **Point in time and invariants:**
  - The series is read as of the job's `cutoff_at` (BR-157) through the compiler with the requester's or owner's permissions (invariant 13).
  - Training data is never copied into the model beyond what the artifact needs.
  - The schema's publish guard (forecast.sql 82–135) is the gate; the trainer does not get around it.
- **Licences:** the libraries are `statsforecast` (Apache-2.0) and `pandas`/`numpy` (BSD). They have to pass the repo's licence check (c10c3f57: no AGPL, ELv2 or non-commercial).
- **Repo rules (CLAUDE.md):**
  - a new deployable is a directory under `backend/services/` with its Dockerfile, deployed by Ansible (`deploy-local` lists, k8s manifests in `deploy/k8s/dev`);
  - schema changes go through tab3b, the generator and an idempotent alembic revision (the next free number on main, currently after v3_0027);
  - API changes go through tab4, `build_a3` and `make api-types`;
  - events go through the outbox;
  - no new `.sh`;
  - the backlog is updated in the same commits.
- **Resources:** `ml-forecast` asks for little memory (≤ 256 Mi request) and trains one job at a time. It has no GPU.
- **Ownership:**
  - `agentic/**` belongs to data-agent-59. The forecast tool and the agent's prompt change are agreed with them first; whoever they agree with makes the change.
  - `frontend/src/features/ask/**` belongs to data-agent-20. The answer blocks reuse their block types (chart, kpi, callout, markdown with citations). This project adds no new block type unless they agree.
- **Two timelines:** AdventureWorks is fictional, with data from 2010 to 2014. Industry sources are from 2015 to 2026.
- **The AW seed document "Điều 3. Kịch bản dự báo"** says the company does not plan forecasts (the "Forecast" scenario in finance data). That is the company's planning scenario, not a statistical forecast. *(my call)* The document stays as written; editing a seed document to suit the feature would falsify the dataset. Ask's forecast block names itself "dự báo thống kê của hệ thống", so it is never read as the company's plan.

## Decisions

### Trainer *(my call, within the agreed "complete" scope)*

- **Service:** `backend/services/ml_forecast`, a Python 3.11 image with statsforecast. It consumes `forecast.job.state_changed` (status `queued`) from `forecast.outbox` through the same outbox relay pattern the worker uses, and runs as `svc_forecast`.
  - Reading the series: the shared compiler code in `libs/` plus the executor's read path. If the executor stays in worker_agent, the trainer asks worker-agent for the series through an internal call. The builder decides, and must not duplicate the compiler.
- **Backtest:**
  - rolling origin, at least 6 cutoffs for month and quarter (the table CHECK), with horizon = the job's horizon;
  - metrics MASE (against seasonal naive, m = 12 for month and 4 for quarter), sMAPE and coverage of 80% and 95%;
  - in-sample MASE for the overfit bound.
- **Intervals:** each model's own intervals where statsforecast gives them, otherwise conformal from the backtest residuals. `interval_method` records which.
- **Selection:** the lowest backtest MASE among candidates with MASE < 1. If none beats seasonal naive, the job ends `failed` with the reason in words ("không mô hình nào tốt hơn cách đoán theo mùa"), and nothing is submitted.
- **Model card:**
  - the algorithm;
  - the training range;
  - the number of points;
  - the backtest table;
  - the known limits, for example "dữ liệu kết thúc 06/2014; khoảng tin cậy rộng dần theo kỳ".

### Ask

- **Detection:**
  - The rule reader adds a future-intent pack, in Vietnamese and English, in `libs/language`.
  - The agentic planner gets a `forecast_metric(metric, scope, horizon)` tool. It returns the published predictions or `no_model`, and it never trains.
  - The governed path calls the same read function.
- **Matching a question to a model:**
  1. The model's metric must equal the resolved metric.
  2. Its scope must equal the question's filters on allowed dimensions.
  3. If no model matches exactly, the nearest is offered as a follow-up ("Có mô hình cho Doanh thu toàn công ty"), not substituted.
- **Access:** `svc_qa` and `svc_agent` get read access to `forecast.prediction`, `forecast.model_version` and `forecast.forecast_model` (grants in tab3b cross_reads).
- **The answer:**
  - It carries the trust label "Dự báo · mô hình đã duyệt" next to GOVERNED and EXPLORATORY (invariant 14).
  - The figures come from `prediction` rows as `{{F<n>}}` facts, like every other figure.

### Crawl → documents

- **Source settings:** a crawl source gets `output = 'records' | 'documents'` and, for documents, a `collection_id`.
- **Ingestion:** on `acquire.run.published` for a documents source, worker-document creates one document per fetched page:
  - title, source URL, `fetched_at`, the licence line from the source, and the extracted text;
  - the same extractor, chunker and embeddings as for an upload.

  A page fetched again with unchanged content is skipped. A changed page supersedes the old document (`supersedes_id`).
- **Seeded group A sources:**
  - Eurostat bicycle-production news;
  - the EU Urban Mobility Observatory news.

  Their terms URL and licence line are prefilled. The legal review itself is the user's (BR-174).
- **Uploads, group B:** the six free CONEBI 2016–2021 PDFs are a documented manual step, "tải lên Tài liệu ngành". They are not committed to the repo, because "All Rights Reserved".

### Answers to the builder check *(my calls)*

- **Who trains, who sees.**
  - The trainer reads the series as the **metric owner**, with the owner's row and column policies (invariant 13).
  - Each version records the owner's effective policy set over the series' tables as a fingerprint, `scope_policy_hash`, in the model card. This is the existing policy set from `query/scope.py`.
  - Ask shows a published forecast only when two things hold:
    - the asker may use the metric (`blocked_metric_ids`);
    - the asker's own policy set on those tables is **the same or broader** than the training fingerprint, which in practice means no stricter row policy.

    Otherwise the block says "Bạn không có quyền xem dự báo này", and it fails closed.
  - The `svc_qa`/`svc_agent` cross-read grants exist only so that this check and the read can run. The app-level check is the gate.
- **Crawled pages are published without per-page review.**
  - The source's legal review (BR-174) is the human gate.
  - A run of a documents source skips schema and record review. On `completed` it emits `acquire.run.published` with `output = documents`, and worker-document consumes that event.
  - Every page then goes through worker-document's existing checks, the same as an upload: unusable pages are skipped and suspected injections are quarantined.
- **Models seeded for DS-AW.** Two model definitions are seeded, neither trained:
  - company revenue (DOANH_THU, monthly);
  - Bikes revenue (DOANH_THU scoped to category Bikes, monthly).

  The horizon is 12 months from the last month with data.
- **The eval runs untrained.** aw054 "Dự báo doanh thu năm 2015" expects the forecast block's "chưa có mô hình dự báo đã duyệt cho Doanh thu" message, with the link, and no number.
- **The e2e test** trains and approves the company model on the cluster, then expects predictions for the months within the horizon.
- **Past-horizon questions.** A question about periods beyond the horizon is answered for the months that fall inside it. The rest is named: "sau 06/2015 nằm ngoài tầm dự báo 12 tháng".
- **Ownership.**
  - The forecast tool in `agentic/**` and the trust-label change in `features/ask/**` are proposed to their owning sessions, data-agent-59 and data-agent-20, before anyone writes them.
  - If they hand them over, this project writes them.
  - **Trust labels are per block:** the trend block is GOVERNED, the forecast block is the new FORECAST ("Dự báo · mô hình đã duyệt"), and the document block is DOCUMENT.
  - The answer-level label stays the strongest block's label, as `RANK` does today. FORECAST ranks below GOVERNED and above EXPLORATORY.
- **The forecast pages are part of "complete".**
  - Dự báo (ForecastPage), Mô hình (ForecastModelPage) and the job page (ForecastJobPage) move off mocks onto the real API. Endpoints are added or fixed as they need, including `/v1/forecast-jobs/{id}/workbench`.
  - Studio's retrain and submit, Ledger and Fuel stay as they are. Fuel and Ledger are the formula and fuel flow, which is out of scope.
- **Lapsed sources.** When a source's review lapses or its terms change, its documents are excluded from retrieval like expired or replaced documents today (BR-174). They stay stored and visible in the document page with their validity. Block 3 then says no current industry document exists.

## Left to the builder

- **Order of delivery.** Suggested *(my call)*, with the trainer first because it is the long pole:
  1. the trainer, backtest and storage, plus the create-model endpoint and seed;
  2. the inbox proposal and its readable summary;
  3. Ask, in both paths;
  4. crawl → documents and the seeded sources;
  5. eval, e2e and docs.
- How the trainer gets the series (a shared library or an internal call), as long as the compiler is not duplicated.
- The exact layout of the forecast block, within data-agent-20's block types.
- Whether `/v1/forecast-jobs/{id}/workbench`, which the frontend calls and no backend serves, is built or its call removed.
