# Audit: Data foundation / data preparation (Data Agent V3, 2026-11-12)

Paths are relative to `/Users/thiennlinh/Dev/Projects/Data-Agent`. Verdicts: **exists / partial / missing**.

## 1. Connector architecture — partial

| Kind | Declared | Scan/profile | Ingest (copy) | Federated query | Evidence |
|---|---|---|---|---|---|
| postgresql | yes | yes (pg_catalog) | yes (asyncpg server cursor) | yes | `worker_agent/schema_scans.py:419-470`, `worker_ingest/worker.py:308-353` |
| mysql, sqlserver | yes | yes (INFORMATION_SCHEMA) | yes (SSCursor / fetchmany) | yes | `libs/source_drivers/__init__.py:167,236,423-443`, `worker_agent/source_meta.py:1-9` |
| clickhouse | yes | yes (HTTP) | yes (JSONEachRow stream) | yes | `source_drivers/__init__.py:289,340`, `schema_scans.py:349,412` |
| oracle, snowflake, bigquery, databricks | in `SourceKind` + CHECK only | no | no (`not wired for ingestion`) | no driver (`no driver for a … source`) | `catalog/schemas.py:19-35`, `worker.py:173,273`, `source_drivers:337` |
| rest_api | yes, "new" badge | try-only (one GET via Redis handshake, no row written) | **no** | no | `catalog/routes.py:132-140`, `catalog/rest_try.py:1-12`, `worker_agent/rest_tries.py:1-16`; RES-20 todo |
| file (CSV/XLSX upload) | yes | at upload (analyze/map/commit) | yes → `workspace.*` | — | `ingest/routes.py`, `worker_agent/uploads.py:22-27`, `libs/tabular/__init__.py:1-9` |
| kafka_cdc, object_store, sftp, sharepoint | enum only | no | no (`mode=cdc` is **poll-CDC**: incremental on a `*/1 * * * *` Temporal schedule, not log CDC) | no | `ingest/routes.py:260-263` |
| web crawl | separate schema `acquire.crawl_source` (not a `catalog.source`) | — | crawler → `extracted_record` → human publish | — | `crawler/runs.py:1-10`, DDL `dataagent.sql:5488-5935` |

Registration: no plugin registry. A kind is (a) the `SourceKind` literal + DB CHECK (`dataagent.sql:1155-1157`), (b) a hard-coded `CONNECTORS` card tuple for the picker (`catalog/routes.py:97-153`, 5 cards only), (c) `if source.kind == …` dispatch in `source_drivers.read/stream`, `worker.py:_QUOTED`, `schema_scans._scan_source`. Adding an engine means touching all four.

Credentials: DB row keeps only `secret_ref` (`catalog.source.secret_ref NOT NULL`, `dataagent.sql:1132`); api writes KV v2 at `secret/tenants/{org}/sources/{source}/account-{uuid7}` with a write-only AppRole (`catalog/vault.py:15-48`); workers read it with their own reader (`worker_ingest/secrets.py`, `worker_agent/query/secrets.py`), `StaticReader` for tests. TLS fails closed to `verify-full`, SQL Server refuses verify modes (`source_drivers:8-13,60-68`). **Exists.**

## 2. Batch vs incremental — exists (poll-based), log CDC missing

- Modes `full | incremental | cdc` (`ingestion_job.mode` CHECK). `cdc` shares the incremental window on a dense schedule (`worker.py:542-544`).
- Cursor: upper bound `MAX(cursor)` fixed before reading (`_max_cursor`, `worker.py:240-291`); window `(cursor_value, upto]`, backfill `[from,to]` (`Window`, 122-131); cursor type must be number/date/timestamp — checked in `pg_attribute` for Postgres (RES-18), only by literal shape on other drivers (`_literal`, 140-151); `skip_today` (435-445); cursor and rows committed in the same session, cursor advanced only on success (635-645).
- Merge: `merge_key_columns` → `public.da_materialize_upsert(... ON CONFLICT DO UPDATE)` (`v3_0035:82-170`; `worker.py:407-420`). Note: this function exists only in the migration, not in `backend/schema/csdl/routines/workspace.sql` / generated `dataagent.sql` (grep) — generator and migration have diverged.
- Streaming: batch `INGEST_BATCH_ROWS=5000`, ceiling `INGEST_MAX_ROWS=2,000,000` → run fails with "ingest it incrementally" (`worker.py:103-106,606-609`). Full mode: first batch `replace=True` → `DROP TABLE IF EXISTS` (`routines/workspace.sql:54`), later batches append — a failed full run mid-way leaves a partial table (no swap/staging table).
- Drift: `check_drift` — gained column added (v3_0030 `ALTER TABLE … ADD COLUMN`), dropped column kept NULL, **retyped column → `SchemaDrift`**, run `status=schema_drift`, job `status=schema_drift` leaves the schedule until a person acts (`worker.py:384-394,483-505`). `on_schema_change=pause_and_ask` pauses on new columns (593-604). Type is inferred from the **first batch's Python values** on non-Postgres drivers; a later value that does not fit is a ValueError (186-203) → rows quarantined to `dlq_record` under `on_invalid_record=quarantine_30d` (782-824). Timestamps/JSON land as `text` (`_WORKSPACE_TYPES`, 37-43) — a silent type downgrade.
- Failures: `failure_kind ∈ config|transient|system` (`worker.py:448-480`, v3_0029); only transient re-queues per `retry_policy backoff_3|once|none` (`_maybe_retry`, 842-848).
- Row counts only (`rows_read = rows_written`, 650-653, even when rows went to DLQ) — `rows_written` is wrong by `rows_dlq`.

## 3. Schema discovery & profiling — partial

Collected per column (Postgres): one aggregate per 100 columns `count, nulls, count(DISTINCT), min, max`, `TABLESAMPLE SYSTEM` when `reltuples > PROFILE_SAMPLE_ABOVE=200k`, top values (`TOP_VALUES`) from the sample, every value of a category column (≤ `MAX_CATEGORY_CARDINALITY`) from the full table for the value index (`schema_scans.py:548-631`). Stored in `catalog.column_profile(sample_rows, null_ratio, distinct_est, min_value, max_value, top_values, pattern_hint)` (`dataagent.sql:1362-1384`). `pattern_hint` is never written (grep). MySQL/SQL Server via sqlglot-rendered queries (`source_meta.py`), ClickHouse HTTP. Not collected: histograms/quantiles, mean/stddev, string length stats, freshness from `pg_stat_user_tables` (RES-23 "còn"), row-level samples (by design).

Keys: PK from `pg_constraint`/`INFORMATION_SCHEMA` → `column_meta.is_key`; FKs read (`schema_scans.py:467+`, `source_meta.py:84-121`) and turned into `context.relationship(origin='inferred', status='pending_review')` deduplicated against non-rejected rows, event `relationship.created` (`schema_scans.py:840-872,965-1010`). **Only declared FKs** — no statistical/name-based join inference, no LLM join hints (grep `origin="llm"` → none). Column/dataset descriptions have `description_status suggested|approved` but no writer sets `suggested` (grep) — the LLM-proposes-description path is schema-only.

Schema change (RES-22): canonical hash; additive → new `dataset_version` at once; breaking → `schema_change` proposal, applied only if still at that version (`schema_changes.py:1-14`). Scan is per-table savepointed → `tables_skipped`, status `partial` (`schema_scans.py:930-931`).

PII: `libs/pii.column_decision` with per-strength share and distinct thresholds (`pii/__init__.py:289-310`); scan redacts `top_values`, `min/max` of personal columns (`schema_scans.py:171-198`); `column_meta.pii_label/pii_confirmed`. Evidence (share, distinct) is not persisted (RES-24 "còn").

## 4. Data quality — partial

- Model: `ingest.quality_rule(kind, params, severity, origin manual|agent_suggested, status)`, `ingest.quality_result(run_kind job_run|pipeline_run|scheduled|manual, passed, violations, sample_ref, acknowledged_*)` (`dataagent.sql:5409-5461`).
- Kinds: `not_null, range, unique, in_list, sum_matches, freshness, custom_sql`, each compiled to one read-only violation count through the executor (`worker_agent/quality.py:1-20,70-146`). Run on create/update, daily, after a pipeline materialize (`pipelines.py:482-485`).
- Effect: `dataset.quality_state unknown|ok|warning|blocked` (`quality.py:293-331`); `blocked` only from a failing **high** rule; acknowledgement with a note (`ingest/routes.py:1620-1628`). On answers: the agentic engine adds a **caveat** text only (`agentic/signals.py:5-7,57-64`); freshness `overdue|missing` from `data_product_member.freshness_state` hands over to the governed path which masks figures (BR-178). The governed SQL path and reports do **not** read `quality_state` (grep `report_runs.py`, `sqlgen/*`, `semantic/loader.py` → nothing) — BR-116 "holds table out of reports" is not enforced in code beyond the caveat.
- Incidents: none (RES-26 todo). No rule suites/expectation packs, no profiling-derived auto rules, no duplicate-row or outlier rule (`unique` is per-column; `skills/anomaly.py` is an answer-time analysis skill, not DQ). Freshness: rule `freshness` on a column + SLA on `data_product_member` (`sla_kind schedule|max_age`, `next_due_at`, `freshness_state`).

## 5. Data preparation pipelines — partial, AUD-04 open

- Steps are SQL only: kinds `filter, dedupe, join, compute, aggregate, cast, quality_rule` (`prep.py:44`), max 20; `language: python` is accepted by the schema (`pipeline/schemas.py:39`) but skipped by the read-only check (`pipeline/routes.py:109-112`) and refused at run time (`pipelines.py:319,338`): a python step is a half-wired door (code_sandbox exists only for the agent). Steps run as chained CTEs (`t` = previous step) in one read-only SELECT through the executor on the inputs' source (`pipelines.py:1-10,59-105`); totals check: non-dropping steps must preserve every money total (BR-23/E2, 201-258). Materialize ≤ `MATERIALIZE_ROWS=200k` via `da_materialize_upload(..., true)` → derived dataset + `lineage_edge` (365-470).
- Agent proposals (`prep.py`): LLM gets column names (never rows), output checked by `check_steps` (kind, read-only SQL, drop condition, reads only inputs/`t`, LIMIT 0 compile) and written **directly** to `pipeline.steps` under `FOR UPDATE` with an OCC compare against the steps it saw (`prep.py:77-116,262-285`). No draft/approval; api PATCH also writes `steps` on an `active` pipeline with only optional If-Match (`pipeline/routes.py:204,540-547`). **AUD-04 confirmed open.**
- Approval exists elsewhere: pipeline `data_product` publish needs owner ≠ drafter (`routes.py:1382-1466`).

## 6. Immutability, versioning, provenance, lineage — partial

- Raw objects: `raw`, `da-raw-crawl`, `da-period-close` created `--with-lock` + default retention (`minio-init.yaml:77-96`, GOVERNANCE dev / COMPLIANCE prod); **`da-ingest` and `da-raw-docs` not locked** (line 20 bucket list vs 77-89) and `DELETE /v1/uploads/{id}` deletes the object (`ingest/routes.py:1288`) — AUD-08 open.
- Workspace tables: upload commit and full ingest both `DROP TABLE IF EXISTS` (`routines/workspace.sql:54`; `uploads.py:249-255`; `worker.py:632`) — previous data gone, `dataset_version` rows survive but point at nothing (AUD-07 partially done: ingest now sets `origin/source_id`, bumps version on added columns `worker.py:674-742`; uploads always `dataset_version=1`, `uploads.py:360`).
- `catalog.dataset_version(schema_hash, diff, change_kind initial|additive|breaking, valid_from/to, certified_at/by)` + `dataset_version_certification(certify|revoke, reason)`; certification only for **consolidated** datasets by the steward, four-eyes (`catalog/routes.py:1436-1528`, BR-156).
- Receipt: `qa.answer.dataset_versions=[{dataset_id, version}]`, `as_of`, `freshness_state`, `result_sha256`, `AnswerReceipt.sealed_at` in the same transaction (`questions.py:2596-2613`). `as_of` is the question's business date, not a point-in-time read of the table: the version pin is nominal because the table content is overwritten in place.
- Lineage: `pipeline.lineage_edge(from_kind,to_kind ∈ source|dataset|column|metric|tile|report|skill|agent|data_product, via, valid_to)` written by `pipelines.py:458` and `derived.py:156`; source→dataset edges derived at read time (`pipeline/routes.py:1028-1030`). No SQL-parsed column lineage (`libs/sql_guard` has `tables_of` only, `__init__.py:174`; RES-27 todo); ingest jobs write no edge.

## 7. Catalog model — exists

`catalog.*` (`dataagent.sql`): `source` (kind, mode federated|ingest, authority_level, secret_ref, scan_schemas, scan_cron, profile_cron, status draft→testing→active|needs_fix|paused|archived; L1121), `scan_run` (kind schema|profile, trigger, tables_skipped, status incl. partial; L1185), `dataset` (origin source|derived|upload|data_product|consolidated, history_kind none|scd2|snapshot|temporal, business_date_col, knowledge_time_col, enabled, steward_id, scope, expires_at, current_version, quality_state; L1214), `dataset_version` (L1269), `dataset_version_certification` (L1303), `column_meta` (dataset_version/removed_in_version, is_key, normalized_type, unit, pii_label, pii_confirmed, description_status; L1323), `column_profile` (L1362), `outbox`. Migration `v3_0014_catalog_asset.py` also exists (not inspected). Data products live in `context.data_product` (owner_id, steward_id, classification public…restricted, freshness_statement, eval_set_id, status draft→pending_review→active|suspended|retired) and `context.data_product_member` (member_kind dataset|metric|knowledge_page|document_collection|shared_product, SLA, freshness_state, per-member classification; L2043-2167). Business glossary: `context.glossary_term`, `synonym`, `value_index`. No tags/domains, no column-level classification beyond `pii_label`, no usage stats.

## 8. Dedup / entity resolution / conflict — partial, crawl-only

- Structured datasets: `dedupe` pipeline step (SQL, user-authored) and `derive_requested` "without duplicate rows" (`derived.py:3-4`); nothing automatic.
- `acquire.entity_link(method exact|fuzzy|human, match_score, status)` + `reference_entity(canonical_key SBV|BIC|MST…)` exist and the api lists/decides links (`acquire/routes.py:2254,2308`), but **no matcher writes `entity_link`** and no service consumes `acquire.consolidation.started` (grep). Reference entities are Vietnamese-bank specific.
- `conflict.conflict` rows are created only from document claims (`documents/claims.py:213`); no detector for figures disagreeing across structured sources.

## 9. Publication contract — exists (product), partial (dataset)

Flow: source test (`catalog/routes.py:689`) → scan (deterministic) → dataset `enabled` → semantic entities/metrics (human; metric versions certified) → data product draft (`routes_products.py:519`) → `change_proposed` proposal → reviewer accepts in `reviews.py:281-301` only if eval run ≥ `eval_set.pass_threshold`, a member metric is certified and the primary dataset has an SLA (`_still_at_the_bar`, 235-272) → `active`. Ask reads `Dataset.enabled` (`semantic/loader.py:104`) and the certified-or-current metric version (159-180); answers tag `product_scope in|out_of_scope` (`questions.py:2457`). Deterministic: scans, hash, drift, DQ counts, read-only guard, totals check, eval gate, two-people triggers. LLM: prep steps, (elsewhere) SQL. Human: relationship review, schema-change proposals, certification, product review, DQ acknowledgement, crawl publish. There is no dataset-level "ready"/contract (RES-28 todo): an `enabled` dataset with `quality_state=blocked` is still queryable.

## 10. Tests

Unit (`tests/unit/worker_ingest/test_worker.py`, `test_secrets.py`; `tests/unit/worker_agent/`: `test_schema_scans.py`, `test_schema_scan_pii.py`, `test_schema_changes.py`, `test_schema_clickhouse.py`, `test_source_meta.py`, `test_source_clickhouse.py`, `test_source_drivers_tls.py`, `test_quality_sql.py`, `test_pipeline_sql.py`, `test_tabular.py`, `test_upload_reader.py`, `test_ingest_profile.py`, `test_rest_tries.py`; `tests/unit/api/`: `test_catalogue.py`, `test_source_credentials.py`, `test_upload_column_types.py`, `test_rest_try_channel.py`, `test_mart_model.py`) — SQL rendering, drift, hash, PII decisions, TLS fail-closed, type inference.
Integration against a real Postgres (ddl_server from `tests/integration/migrate/conftest.py`, imported by `tests/integration/worker_ingest/conftest.py:11`): `worker_ingest/test_worker.py` (cursor window, additive columns, retyped stop — e.g. `test_an_incremental_job_takes_new_columns_and_stops_on_a_retyped_one`), `worker_ingest/test_mysql_ingest.py` (testcontainers MySQL, skipped if absent, L29-34); `worker_agent/`: `test_schema_scans.py`, `test_uploads.py`, `test_pipelines.py`, `test_prep.py`, `test_derived.py`, `test_quality_schedule.py`, `test_mysql_source.py`, `test_product_use.py`, `test_adventureworks_star_schema.py`; `api/`: `test_catalog.py`, `test_ingest.py`, `test_ingest_dry_run.py`, `test_pipeline.py`, `test_pipeline_reads_marts.py`, `test_context_products.py`, `test_conflict.py`, `test_acquire_sources.py`. No test for SQL Server, Snowflake/BigQuery (no code), REST load, consolidation, entity links.

## 11. Top 10 gaps vs an enterprise data foundation

| # | Gap | Sev | Where the fix goes |
|---|---|---|---|
| 1 | In-place `DROP TABLE` on full ingest and upload commit: no retained versions, receipts pin versions whose data is gone; not point-in-time | P1 | `backend/schema/csdl/routines/workspace.sql` (versioned table + view swap), `worker_ingest/worker.py:632`, `worker_agent/uploads.py:255` |
| 2 | Agent-proposed prep steps written straight to active/scheduled pipelines; PATCH steps on active with optional If-Match (AUD-04/09) | P1 | `worker_agent/prep.py:262-285`, `api/modules/pipeline/routes.py:540-547` |
| 3 | `quality_state=blocked` and DQ results never gate governed SQL/reports/products — only a caveat string | P1 | `worker_agent/semantic/loader.py:104`, `worker_agent/report_runs.py`, `reviews.py:_still_at_the_bar` |
| 4 | Four of eight "queryable" kinds (oracle, snowflake, bigquery, databricks) and REST/kafka/object_store/sftp/sharepoint are enum-only; no connector registry | P1 | `libs/source_drivers/__init__.py` (registry by kind), `catalog/routes.py:97` cards from it |
| 5 | Drift on non-Postgres engines typed from first batch's values; timestamp/json/uuid stored as text; `rows_written` ignores DLQ | P2 | `worker_ingest/worker.py:37-43,186-203,650-653` |
| 6 | No column-level or SQL-parsed lineage; ingest jobs write no edge; source→dataset edges synthesised at read time | P2 | `libs/sql_guard/lineage.py` (new, sqlglot), `worker_ingest/worker.py` after catalogue, `pipeline/routes.py:1028` |
| 7 | Profiling depth: no histograms/quantiles/length/pattern stats, `pattern_hint` unused, no freshness from DB stats, PII evidence not stored | P2 | `worker_agent/schema_scans.py:556-631`, `column_profile` DDL |
| 8 | No DQ incidents, suites, auto-generated rules from profiles, duplicate-row or outlier checks; no dataset contract | P2 | `worker_agent/quality.py`, `ingest.quality_*` DDL, RES-26/28 |
| 9 | Entity resolution/consolidation is schema-only: no matcher writes `entity_link`, no consumer of `acquire.consolidation.started`; reference entities VN-bank-specific | P2 | `backend/services/crawler/` or `worker_agent/` new handler; `acquire.reference_entity` CHECKs |
| 10 | Raw buckets `da-ingest`, `da-raw-docs` unlocked and upload delete removes the object; `da_materialize_upsert` lives only in migration v3_0035, not in the schema generator | P2/P3 | `deploy/k8s/*/job/minio-init.yaml:77-96`, `ingest/routes.py:1288`; `backend/schema/csdl/routines/workspace.sql` |

Also observed: relationship inference = declared FKs only (no statistical join discovery, RES-27); `description_status='suggested'` has no writer; `pause_and_ask` on new columns pauses the job but opens no proposal (unlike scan-side `schema_changes.propose`).
