# OpenMetadata ingestion, clean-room study: what it does, what we should build

Source read: sparse clone `om-sparse/` at `d77e1fe709b4`. Date: 03/11/2026. The `OpenMetadata/` full-clone directory is empty, and the sparse clone has no `ingestion/tests`, so every behaviour below comes from reading source, not tests.

## 0. Licence map (read this first)

| Area read | Licence | What we may do |
| --- | --- | --- |
| `ingestion/src/metadata/**` (lineage, usage, profiler, sampler, data_quality, pii, dbt, semantic metrics, topology runner, status): 1223 of 1587 `.py` files carry the "Collate Community License, Version 1.0" header, and the rest are `__init__`/generated files in the same tree | **Collate Community License 1.0.** It forbids use in a competing SaaS | Learn behaviour only. **No code, no regex lists, no SQL templates, no docstring prose.** Everything below is paraphrased. Pseudo-code is mine |
| `openmetadata-spec/src/main/resources/json/schema/**` (`tests/basic.json`, `type/recognizerFeedback.json`, `metadataIngestion/*.json`) | Apache-2.0 | May be quoted, with attribution |
| `openmetadata-service/src/main/java/**` (`AccessControlDAOs.java` usage-percentile SQL) | Apache-2.0 (header "Licensed under the Apache License, Version 2.0") | May be reused with attribution. We will still write our own, since it is a textbook percent-rank |

Third-party libraries OM depends on (`collate-sqllineage`, `sqlglot`, `sqlfluff`, `sqlparse`, Presidio, spaCy) have their own licences. The Collate fork is a separate package, and we do not need it: we already have sqlglot.

---

## 1. Query-log lineage and usage

### 1.1 How it works

**Pipeline shape.** One workflow has two modes, lineage and usage, over the same per-dialect "query parser source":

1. Source: a dialect-specific SQL statement reads the warehouse's query history for a time window, with a `result_limit`. It appends a dialect-specific `filters` fragment and an optional user `filterCondition`.
2. Lineage mode parses each query and emits table edges and column edges.
3. Usage mode: a Processor parses each query into "tables touched plus joins". A Stage aggregates per (table, day) into local files. A BulkSink resolves tables, publishes usage counts, join counts, query entities, and "life cycle" (last created/updated/accessed per table), then asks the server to compute percentile ranks.

**Excluding its own and dbt's queries.** Every query OM itself runs carries a leading JSON comment, `/* {"app": "OpenMetadata", ...} */`. Every history query excludes rows whose text starts with that marker, or with dbt's equivalent `{"app": "dbt"}` comment, using a `NOT LIKE` on the comment prefix. (MSSQL matches it anywhere in the text, because the plan cache may prepend text.) It is a cheap, dialect-free way to tag your own traffic.

**Log sources per dialect** (paraphrased, not the templates):

| Dialect | Where history comes from | Notes / filters |
| --- | --- | --- |
| Postgres | `pg_stat_statements` (configurable source), joined to `pg_database` and `pg_user` | The duration column is `total_exec_time` on PG13+ and `total_time` before; it is probed at connect time. The lineage filter keeps text matching ILIKE patterns for CTAS, `insert…into…select`, `update`, `merge`. **No timestamps**: analysisDate is "now" |
| Snowflake | `ACCOUNT_USAGE.QUERY_HISTORY`, windowed on `start_time`, paged with `LIMIT/OFFSET` | Carries query_type, user, db, schema, elapsed time, and cloud-services credits × a configured credit price ("cost"). Usage drops session/DDL/admin query types (ROLLBACK, USE, SHOW, DESCRIBE, GRANT-ish, COPY, PUT/GET…). Lineage keeps MERGE/UPDATE/CTAS/COPY/UNLOAD, INSERT-with-SELECT, `ALTER TABLE … SWAP`, CREATE TABLE … CLONE, CREATE TEMPORARY VIEW |
| BigQuery | `region-X.INFORMATION_SCHEMA.JOBS_BY_PROJECT` | Lineage keeps statement_type MERGE/CTAS/UPDATE, INSERT…SELECT, CREATE TABLE … CLONE / LIKE. A separate JOBS query gives DML row counts for freshness (§2) |
| Redshift | Provisioned: `stl_query`, with text rebuilt from `stl_querytext` chunks via ordered string aggregation (a chunk cap keeps the 65 535-char limit), joined to `stl_scan` for the tables read. Serverless: `SYS_QUERY_HISTORY` | Drops `userid ≤ 1`, labels maintenance/metrics/health, aborted queries, cursor `fetch`, sampling and "Undoing transactions" noise. Dedupes querytext rows first because duplicates break the aggregation |
| MSSQL | Default: plan cache (`dm_exec_cached_plans` × `dm_exec_query_stats` × `dm_exec_sql_text`), excluding `Prepared` objtype. Alternative: **Query Store**, restricted to ad-hoc statements (`object_id = 0`), so statements inside procedures are not double counted | Usage drops text containing create procedure/function or `declare` |
| MySQL | `mysql.general_log` (`command_type='Query'`) or `slow_log` | No user, no db, no duration. Lineage filters text the same way as the others |

**Stored procedures.** One source query returns, per executed procedure, the child statements it ran. On Snowflake, CALL rows are correlated with non-CALL rows of the same `SESSION_ID` whose start time falls inside the CALL's window. Matching a query back to a procedure entity is careful: by name alone if unique; if the name exists in several db/schemas, by the query's reported db/schema; and **if still ambiguous, no lineage at all** rather than a wrong one. Each child statement goes through the same lineage parser, tagged with the procedure as the "pipeline" on the edge.

**The parser (`LineageParser`).** Behaviour in my words:

```
parse(query, dialect, timeout=30s, parser_type=auto):
  q = clean(query)        # strip Snowflake " copy grants ", unescape "\n",
                          # cut MERGE "when matched…" tail, drop non-lineage
                          # statements: plain COPY (keep stage COPY INTO @…),
                          # CREATE TRIGGER, CREATE FUNCTION/PROCEDURE  -> None
  for engine in [sqlglot, sqlfluff(dialect), sqlparse]:   # filtered by parser_type
      try under timeout: run engine, force column lineage + table sets
      on success: return engine     # FIRST SUCCESS WINS, no scoring
  return None (record failure reason)
  masked = mask_literals(q)         # done with the same engine's tree
```

The "best parser" is really "first parser that does not throw or time out". There is no comparison of results.

**Column lineage.** The engine returns column paths (source → intermediates → target). OM keeps only the first and last element of each path, so intermediates such as CTE columns collapse. Two refinements:
- **Star handling:** a `("*","*")` pair (from `SELECT *` or `INSERT … SELECT *`) is expanded to same-name pairs for every column of the resolved source table. A `COUNT(*)` that yields (alias, `*`) must not expand.
- Pairs are resolved to column FQNs only if both sides exist on the resolved entities. Unknown columns are dropped silently.

**Table resolution and cross-database lineage.** Raw names (`db.schema.table`, with `<default>` placeholders) are searched in the catalogue under a list of service names, with an LRU cache keyed by (service, db, schema, table). Options: `schema_fallback`, synonym/alias resolution (opt-in per service), and per-engine normalisation (ClickHouse has no database level). Cross-database lineage simply passes several services into the same search, and emits edges with source `CrossDatabaseLineage`. An edge is the Cartesian product of all matching from-entities and to-entities, so an ambiguous name yields several edges.

**Temp-table lineage** (`enableTempTableLineage`). This is clever:

```
graph = DiGraph()
for each query: for each (src, tgt) the parser found:
    if src or tgt does not resolve to a catalogue table:
        graph.add_edge(src, tgt) with node attr fqns=[resolved…] or []
        (do NOT emit an edge now)
after all queries:
    for each weakly connected component:
        paths = all simple paths root->leaf, cutoff 20 nodes, 30 s per root
        walk each path; keep "last resolved node"; when we reach the next
        resolved node, emit edge(last_resolved -> this) and record the
        unresolved chain in between as tempLineageTables hops
        dedupe (from,to) pairs across paths
```

So `real_a → #tmp1 → #tmp2 → real_b` becomes one edge, `real_a → real_b`, annotated with the temporary hops. Procedures get one graph each.

**Join extraction (usage).** Independent of the lineage engine: the statement is re-tokenised with sqlparse. Every `Comparison` token (including inside parentheses) whose two sides both contain a dot is resolved: split into up to 4 parts (db.schema.table_or_alias.column), map alias → table via the parser's alias map, else try the bare, `schema.table`, and `db.schema.table` forms. Only left→right is stored (the server symmetrises). In the bulk sink, joins are aggregated per (table, column, joined column FQN) into `joinCount`.

**Literal masking.** Walk the parse tree and replace every string, number, or float literal with `?`, except:
- integer literals in GROUP BY / ORDER BY (ordinal references `ORDER BY 1`); the context resets on any other keyword but survives GROUPING SETS / CUBE / ROLLUP;
- string arguments of sequence functions (`NEXTVAL('seq')`, CURRVAL, SETVAL, LASTVAL);
- literals inside a comparison, even in HAVING, are always masked.

There is an LRU cache of 128 masked queries. Masked text is what lineage edges carry. **Note:** the usage stage builds query entities from the raw SQL, not the masked text (see "breaks").

**Usage aggregation and percentile rank.** The Stage counts +1 per parsed query per (table, day), appends the joins, and records query cost per (query hash, day) as a sum of cost and duration plus a count. On the server (Apache-2.0 Java), per entity and day: `count1`, `count7` (= today + sum of the last 6 days of count1), and `count30`. Upserting the same day replaces count1 and adjusts count7 and count30 by the delta. Then, per entity type and date: `percentileN = round(100 * (#entities with countN < mine) / #entities that day, 2)`. That is a plain percent-rank, computed with three correlated `COUNT(*)` subqueries.

**Life cycle.** From each query's type (create/update/delete/select…), keep per table the most recent "created", "updated", and "accessed" timestamps and users. This gives freshness and staleness without the profiler.

**Parallelism and limits.** Queries are chunked by 200. The per-query timeout is 300 s, the process timeout is chunk × per-query, and at most 10 "timed-out threads" may be active at once. Multiprocessing is coded but **hard-disabled** (`multiprocessing_supported = False`).

### 1.2 What is clever
- Self-tagging queries with a JSON comment, and filtering that tag out of every log.
- The temp-table graph collapse, plus emitting the hops as evidence.
- Refusing to attach procedure lineage when a name is ambiguous.
- Keeping GROUP BY/ORDER BY ordinals unmasked, so a masked query stays semantically identical and dedupes well.
- Running percent-rank per entity type and day, so "top 5% most used tables" is one lookup.
- The life-cycle timestamps derived from query types.

### 1.3 What breaks
- **"Best parser" is first success.** A sqlglot run that parses but resolves fewer tables or columns beats a richer sqlfluff run. Nothing compares coverage.
- **Timeouts are no-ops in threads.** The timeout is SIGALRM-based and only armed on the main thread (and not on Windows). In threaded paths a pathological query can hang a worker indefinitely.
- **`pg_stat_statements` semantics are wrong for usage.** It is cumulative since the last reset and has no timestamps. OM counts each normalised statement as 1 "today" on every run (not `calls`), and takes `LIMIT n` with no ORDER BY, which gives an arbitrary subset. Daily usage for Postgres is therefore "distinct statements still in the view", repeated every day.
- The MSSQL plan cache loses evicted plans. Only Query Store is durable.
- ILIKE '%update%' lineage filters match any text containing the word (comments, column names such as `last_update`).
- Join extraction misses `USING`, `NATURAL`, unqualified column comparisons, and joins whose both sides lack a dot. It also counts any qualified equality in WHERE, including non-join filters between two tables' columns.
- Star expansion assumes same-name columns, which is wrong for `INSERT INTO t SELECT * FROM s` when column order differs.
- Raw SQL with literals (possibly PII) is persisted as query entities.

### 1.4 Clean-room design for us

We already have `libs/sql_guard` (sqlglot: `check_read_only`, `tables_of`, `dialect_of`). sqlglot alone is enough: `sqlglot.lineage` handles column lineage and its `optimizer.qualify` resolves aliases. No fallback cascade.

**New `backend/libs/sql_guard/lineage.py` (M)**
```
analyse(sql, dialect, schema: dict|None, budget_s=10) -> Analysis | Failure
  run in a ProcessPoolExecutor worker (real timeout via future.result(timeout))
  tree = parse_one(sql, dialect); reject non-DML/DDL-with-select kinds
  qualify(tree, schema=schema)          # resolves aliases, expands * when schema known
  reads   = tables in FROM/JOIN minus CTE names minus write target
  writes  = INSERT/MERGE/CTAS/UPDATE target
  columns = for each projected column of a write: sqlglot.lineage(col) leaf nodes
  joins   = for each exp.Join: ON-equalities (exp.EQ with Column on both sides
            from different tables) + USING columns; plus WHERE equalities only
            when the two tables are both in FROM without explicit JOIN
  fingerprint = sha256(masked(tree).sql(dialect, normalize=True))
  masked   = transform(tree, Literal -> Placeholder) except
             ordinals under Group/Order, sequence-function args
  coverage = (#resolved tables, #column edges)   # recorded, used for review
```
Passing the catalogue schema to `qualify` gives us a correct `*` expansion, which OM approximates.

**New `backend/services/worker_agent/query_log.py` (M)**: per-dialect readers, written from vendor docs.
- Postgres: **snapshot deltas**. Store the previous `(queryid, dbid, userid) → calls, total_exec_time`. Usage for the window = current − previous, which also handles `pg_stat_statements_reset()` (negative delta means a reset: take current). This fixes OM's main Postgres flaw.
- Snowflake, BigQuery, Redshift-serverless, MSSQL Query Store, MySQL slow_log: windowed by the last successful watermark minus a safety margin (§6).
- Every connection we open already sets `application_name` (e.g. `dataagent-schema-profile`). Add a leading `/* dataagent:<run_id> */` comment to every SQL we send, and exclude both markers (ours and dbt's) at the log reader.

**Data model (alembic, the next free migration number)**
- `catalog.query_fingerprint(id, org_id, source_id, fingerprint, masked_sql, statement_kind, first_seen, last_seen)`. Only masked SQL is stored, run through `libs/pii.mask` as well (invariant: no raw literals).
- `catalog.dataset_usage_daily(org_id, dataset_id, day, executions, distinct_users, cost, percentile_1, percentile_7, percentile_30)`. Compute percentiles with `PERCENT_RANK() OVER (PARTITION BY org_id, day ORDER BY executions_7d)`, not with correlated counts.
- Lineage: write `pipeline.lineage_edge` rows `from_kind='dataset'|'column'`, `via='query'`, `via_ref=query_fingerprint.id`. The emitting event goes through the outbox (ADR-06). Temp-table collapse: keep unresolved nodes in an in-memory networkx graph per run, and store hops in a new `hops jsonb` column on the edge (or a sidecar table).
- Joins → `context.relationship(origin='inferred', status='pending_review')`, with new columns `evidence='query_log'` and `join_count`. Compute `overlap_ratio` only after the proposal, with a bounded `COUNT DISTINCT` overlap query in the profile pass. The steward still decides (invariant: LLM/heuristics propose, a human decides).
- Procedure attribution: follow OM's rule, **no edge when the name is ambiguous**.

---

## 2. Profiler

### 2.1 How it works

**Metric taxonomy.** Each metric is one of these kinds:

| Kind | Meaning | Examples |
| --- | --- | --- |
| Static | A pure aggregate, so all of a column's static metrics go into **one SELECT** | count, null count, distinct, unique, min, max, mean, sum, stddev, min/max length, like/regex counts |
| Window | Needs a separate query | median, Q1, Q3 |
| Composed | Pure Python over other results | null ratio, distinct ratio, unique ratio, duplicate count, IQR = Q3−Q1, non-parametric skew = (mean − median)/stddev |
| Hybrid | Needs earlier results and a new query | histogram, cardinality distribution |
| Query | A custom query | — |
| System | Warehouse DML logs | inserts, updates, deletes |
| Table | Table-level metrics | row count, column count, size |

Default set: row count, column count/names, median, Q1, Q3, mean, values count, distinct count/proportion, min, max, null count/proportion, stddev, sum, unique count/proportion, IQR, non-parametric skew.

**Overflow fallback.** If the batched static query fails with a programming/DB error, it retries the batch **without sum, mean and stddev**, which overflow on huge numerics. One bad metric does not lose the whole column.

**Row count from the catalogue first.** Per dialect: Postgres `pg_class.reltuples` + `pg_total_relation_size`; MySQL `information_schema.TABLES.TABLE_ROWS`; MSSQL `sys.dm_db_partition_stats`, falling back to `sys.partitions`; Snowflake, BigQuery, Redshift, Oracle, ClickHouse each have their own stats views. Falls back to `COUNT(*)` when the stat is missing, or is 0 on a view. With a partition filter it always uses `COUNT(*)`.

**Sampling.**
- Percentage mode on Postgres and Snowflake uses native `TABLESAMPLE BERNOULLI(p)` by default, or `SYSTEM(p)` if configured (block-level, faster, clumpier). Snowflake also supports `SAMPLE (n ROWS)`.
- Generic dialects add a `random() % 100` column in a CTE and keep `≤ p`.
- Row-count mode: `random % row_count`, ORDER BY that, then LIMIT n (randomised), or a plain LIMIT n.
- If the row count is unknown (0), the sample falls back to 100%.
- Sample data shown to users is capped (`sample_limit`, and a per-cell character cap). JSON/VARIANT values are decoded only below the cap.

**Partitioned tables.** The profiler window is inferred from partition metadata, but only if the column type agrees (some sources mislabel every key as TIME_UNIT). Windows: HOUR → last 24 h; DAY → 3 days (covers ~2-day export lags); MONTH → 35 days; YEAR → 370 days. Integer range partitions default to 1..10 000. Inference is refused, with a log line, when the type disagrees.

**Push-down per dialect.** SQLAlchemy `@compiles` per function: median/quantiles become `percentile_cont(p) WITHIN GROUP` on Postgres/Cockroach, `approx_percentile` on Snowflake/Presto/Athena (Trino wraps it in a count=0 guard), `percentile_approx` on Databricks, ClickHouse `quantile(p)(x)` with a NaN→NULL guard, Druid `APPROX_QUANTILE`, Databend `quantile_cont`. The same pattern exists for random, modulo, length, md5, regexp, and others.

**Histogram.**
```
bins via Freedman–Diaconis: width = 2·IQR·n^(-1/3), k = ceil((max-min)/width)
if IQR unknown or k > 100: Sturges k = ceil(log2(n) + 1), width = (max-min)/k
cap k at 100
one SELECT with k COUNT(CASE WHEN lo<=x<hi) columns (last bin open-ended)
string columns: histogram over LENGTH(x)
```
**Cardinality distribution (categorical).** Value counts. A category is kept if it is among the top 5 or holds ≥ 2% of rows; the rest goes to "Others". If distinct ≤ 20, every category is kept exactly.

**System metrics (freshness/volume without scanning).**
- Snowflake: reads `QUERY_HISTORY` DML rows, extracts the target table with a regex on INSERT/UPDATE/MERGE/DELETE, resolves implicit names via `SHOW TABLES` (limit 100), and gets row counts from the history row or `RESULT_SCAN(query_id)`. It reads the history **once per batch of 50 tables**, keeping only the 2 most recent batches in a byte-bounded cache (2 GB).
- Redshift: compares `stl_insert` with `stl_delete` per query id. An insert with no matching delete is an INSERT, a delete with no insert is a DELETE, and both together are an UPDATE (Redshift updates are delete+insert).
- BigQuery: `INFORMATION_SCHEMA.JOBS` `dml_statistics` for the last day.

**Threads/limits.** Thread count is clamped to 1..20, with an auto rule of `max(min(5, tasks), tasks//3)`. The per-table timeout default is 12 h. The profiler workflow forces `successThreshold=80`.

### 2.2 Clever
- One query per column for all static metrics, with an automatic retry minus the overflow-prone ones.
- Catalogue statistics for row count, so a 10 B-row table does not get `COUNT(*)`.
- Partition windows sized to survive ingestion lag.
- Redshift's insert/delete pairing to classify DML.
- Batching a shared log scan across tables.

### 2.3 Breaks
- `pg_class.reltuples` is `-1` for never-analysed tables (PG14+). The code only treats `NULL` (or 0 on a view) as unknown, so a −1 can flow into the row count. I have not verified this against a test.
- `random() % 100 ≤ p` scans the whole table anyway. It does not cut I/O, only downstream work.
- Histogram bins over a *sample* use sample min/max, so outliers outside the sample fall outside the bins.
- Non-parametric skew is undefined at stddev 0 (guarded only by exceptions).

### 2.4 Clean-room design for us

Today, `_profile_source` in `schema_scans.py` runs **two full-table queries per column**: one with `count(*)`, `count(DISTINCT)`, min and max, and one top-values query. It also assumes nothing about size.

Change `backend/services/worker_agent/schema_scans.py` + `source_meta.py` (M):
```
plan_profile(table):
  est = catalog row estimate (pg_class.reltuples; treat <0 as unknown -> ANALYZE-free COUNT capped)
  sample = none if est < 100k else TABLESAMPLE SYSTEM(pct) with pct = clamp(100*target/est, 0.1, 100)
           (BERNOULLI when the table is < 10 GB; SYSTEM above)
  one SELECT per table, batching up to N columns:
     count(*), per column: count(col), count(distinct col) [or HLL/approx on CH/Snowflake],
     min, max, avg/stddev for numerics, min/max(length) for text
  on numeric overflow: retry without sum/avg/stddev
  second pass only for columns that need it: percentile_cont(0.25,0.5,0.75) in one SELECT,
     histogram via FD/Sturges (own code), top-k for category columns
```
Store a time series: a new `catalog.column_profile(dataset_version_id, column, profiled_at, sample_pct, metrics jsonb)` and `catalog.table_profile(dataset_id, profiled_at, row_count, size_bytes, inserts, updates, deletes)`. Keep sensitive-column suppression exactly as today (`_is_sensitive_column`): no min/max/top values.

**Freshness/volume for Postgres** (our main dialect, which OM does not cover): snapshot `pg_stat_user_tables` (`n_tup_ins`, `n_tup_upd`, `n_tup_del`, `n_live_tup`, `last_autoanalyze`) per scan and store the deltas. This is the Postgres equivalent of OM's system metrics. It feeds the `freshness` rule kind and a new `volume_change` signal.

---

## 3. Data-quality runner

### 3.1 How it works

**Execution.** A test definition names a validator class. It is imported dynamically per runner type, `sqlalchemy` or `pandas` (pandas for files/datalakes, iterating DataFrame chunks). The table's test cases are first filtered for compatibility:
- column missing → failure "Incompatible Column";
- nested path → failed cleanly, as not supported;
- data type not in `supportedDataTypes` → failure, where an empty list means "any type".

Runtime parameter setters inject computed context: the sample scope, table-diff params, custom-SQL params. Validators compute metrics with the same metric classes as the profiler, then compare.

**Thresholds (deviation tolerance).** Parameters `threshold` and `thresholdUnit` (ABSOLUTE | PERCENTAGE). Two semantics:
- (a) **row tolerance**: the number of violating rows allowed;
- (b) **statistic tolerance**: range tests widen [min, max] by the threshold, and exact tests accept ±delta.

The widening is always applied **after** bound resolution, so it also applies to dynamically predicted bounds. Guards:
- negative, NaN, or infinite thresholds are rejected and treated as 0;
- PERCENTAGE of a 0 reference stays 0, with a one-time warning;
- ±inf bounds (unset) are left alone.

**Dynamic assertions.** The spec carries `useDynamicAssertion` on the test case and `predictedValue` on each result value (`tests/basic.json`, Apache-2.0: `"predictedValue": {"description": "predicted value", "type": "string"}`). In OSS ingestion, **the learning is not present.** Bounds come from `get_min_bound`/`get_max_bound`, which read static params defaulting to ∓inf, and the code says dynamic validators "override those getters". The predictor is in Collate's closed code. So there is nothing to learn from OM here except where the hook sits: bound resolution, then tolerance, then compare. We must design our own (below).

**Dimensional tests.** `dimensionColumns` + `topDimensions` (default 5, max 50). Two passes:
1. GROUP BY the normalised dimension (cast to string; NULL and the literal string 'NULL' both become the 'NULL' label; string literals are inlined because Trino rejects bind params that appear in both SELECT and GROUP BY). Compute metrics, failed count and impact score; ORDER BY impact DESC; LIMIT N+1.
2. If an (N+1)th row exists, re-aggregate everything `NOT IN top N` as one "Others" row. Validators with special needs supply a custom Others source (unique uses value-count CTEs, median uses a normalised CTE).

**Impact score** (the idea, in my words): severity is the failure rate squared, multiplied by a volume factor and a small-sample damper, then normalised and clamped to [0, 1].
- Volume factor tiers: <10 rows → 0.25, <100 → 0.5, <1 000 → 0.75, <10 000 → 1.0, <100 000 → 1.25, else 1.5. Tiers are used instead of a log because SQLite lacks LOG.
- Damper: min(1, rows/100).
- Normalisation: divide by 1.5.

**Roll-up policy** (`dimensionFailurePolicy`):
- `OVERALL_ONLY` (default): groups are reported, never acted on.
- `ANY_DIMENSION`: a passing test fails if any non-excluded group fails. Groups below `minRowsPerDimension` are excluded, but only under PERCENTAGE thresholds, because 1 bad row in 3 is 33%. Excluded failing groups are still named in the message.
- Aborted results are never rolled up.

**Failed-row samples.** When failed, it fetches up to **50** rows matching the violation filter. It also stores the compiled inspection query with literals bound, so users can re-run it.

**Custom SQL test.** Strategy `ROWS` (the query returns violating rows; count them) or `COUNT` (the query returns a number). The partition filter is injected by rewriting the WHERE clause. Passed/failed rows are computed against the table row count for operators >, <, =.

**Publishing.** Results become time-series `TestCaseResult` rows (status Success/Failed/Aborted, result values, passed/failed counts and percentages, dimension results with min/max bound, impact score, `excludedFromRollUp`) sent to the server. Incidents are opened server-side. An Aborted result (connection failure) is recorded distinctly from Failed.

### 3.2 Clever
- Ordering dimensions by impact rather than by failure rate. This stops "1 of 1 row failed" from topping the list.
- Applying the threshold after bound resolution, so static and learned bounds behave identically.
- The `minRowsPerDimension` exemption tied to PERCENTAGE thresholds only.
- Aborted being separate from Failed.

### 3.3 Breaks
- The impact docstring's own example is wrong: 10 000 rows at 90% is in the ≥10 000 tier (1.25), so the score is 0.81·1.25/1.5 = 0.675, not 0.81. Tier boundaries cause score jumps. A smooth function is better if our DB has `ln` (Postgres does).
- The pandas failed-row sampler can return up to ~2× the cap (it extends by 50 per chunk and checks after).
- The literal-bound inspection query can embed sensitive literals in stored results.
- `NOT IN (top N)` with NULL-normalised strings works only because NULL was relabelled first. Forgetting that normalisation silently drops rows.

### 3.4 Clean-room design for us (`backend/services/worker_agent/quality.py`, 7 kinds today: not_null, unique, range, in_list, sum_matches, freshness, custom_sql)

**(a) Tolerance (S).** Add `threshold`, `threshold_unit` to `ingest.quality_rule`. Rule: violations ≤ tolerance means pass. Range: widen bounds after resolution. Validate finite and ≥ 0.

**(b) Dimensional rules (M).** Add `dimension_column`, `top_dimensions` (default 5, max 50), `failure_policy`, `min_rows_per_dimension`. Generate with sqlglot (as `violation_sql` already does):
```
WITH g AS (SELECT COALESCE(CAST(d AS text),'∅') AS dim, COUNT(*) AS total,
                  SUM(CASE WHEN <violation predicate> THEN 1 ELSE 0 END) AS failed
           FROM t GROUP BY 1)
SELECT dim, total, failed, impact(failed,total) AS impact FROM g ORDER BY impact DESC LIMIT n+1
-- then one Others row over dim NOT IN (top n)
```
Our own impact function: `impact = r^2 · v(total) · min(1, total/100)` with `r = failed/total` and a **smooth** volume term `v = clamp(log10(total+1)/5, 0.2, 1.0)`. It is monotone with no jumps, and we compute it in Postgres or in Python after the query.

**(c) Dynamic bounds (M), our own design.** Store each run's observed metric in `ingest.quality_observation(rule_id, observed_at, value)`. Predict:
```
history = last 30 observations (need >= 8, else static bounds only)
optionally same weekday only, if weekly seasonality detected (autocorr lag7 > 0.5)
center = median(history); spread = 1.4826 * MAD(history)  (robust to past incidents)
spread = max(spread, 0.01*|center|, epsilon)
bounds = center ± k*spread, k = 3 (configurable 2..5)
exclude from history any observation whose result was later marked "incident confirmed"
```
Store `predicted_min/max` on the result. The tolerance from (a) still applies after prediction. Turning dynamic bounds on is a proposal a steward accepts (HITL), never automatic.

**(d) Failed-row samples (S).** At most 50 rows, columns flagged sensitive are dropped, every value goes through `libs/pii.mask`, and the inspection SQL is stored **parameterised**, not with literals bound.

**(e) Aborted vs Failed (S).** We already route failures to a `data_review` HITL request. Add `status='aborted'` for source errors, so a connection blip does not page a steward.

---

## 4. Auto-classification / PII

### 4.1 How it works

**Inputs.** Up to `sampleDataCount` values per column (default 50). The pipeline confidence defaults to **80** (on a 0–100 scale, divided by 100). Each classification has `autoClassificationConfig.minimumConfidence` (0–1), `mutuallyExclusive`, and `conflictResolution` (`highest_confidence` | `highest_priority` | `most_specific`). Tags carry configurable recognizers: predefined Presidio ones, patterns, exact terms, context words. Each recognizer has a target (column_name or content) and an `exceptionList` of entity links.

**Pre-processing.**
- Values become strings: numbers and dates are stringified, bytes are skipped (binary is never classified), dict/list values are flattened, None and blank are dropped, and text is truncated at 5 000 characters.
- Casing is preserved for pattern recognizers (IBAN-like codes).
- A second NER pass runs on **title-cased copies of ALL-CAPS, digit-free values**, because spaCy misses "SERGE DUPONT".

**Gates.**
- **Relative cardinality cutoff 1%**: if unique/total < 0.01, the content looks like a constant or enum and content analysis is skipped. Column-name analysis may still run.
- The score cutoff to report a tag at all is > 0.1.

**Scoring per tag:**
- Content score = **max** recognizer score over values, **not the mean**. The rationale is explicit: one SSN in 50 rows would average to 0.017. For a security control, sensitivity is contaminating, so one confirmed hit classifies the column.
- **Corroboration:** an uncontextualised spaCy PERSON/LOCATION/NRP guess counts only if ≥ 2 *distinct* normalised matches of that entity type exist. Pattern hits, vetted dates, and context-enhanced NER stand alone.
- Column-name score = min(1, sum of name recognizer scores).
- The final score is the max of the two. The column wins ties (`column_wins = column ≥ content`), and the winner's recognizer metadata and patterns are stored on the tag label (`TagLabelRecognizerMetadata`: recognizer id/name, score, target, matched patterns).

**Feedback.** `recognizerFeedback.json` (Apache-2.0 spec) has these feedback types: FALSE_POSITIVE, INCORRECT_CLASSIFICATION, OVERLY_BROAD, CONTEXT_SPECIFIC. Reasons are NOT_SENSITIVE_DATA, WRONG_DATA_TYPE, INTERNAL_IDENTIFIER, PUBLIC_INFORMATION, TEST_DATA, ENCRYPTED_DATA, OTHER. It also has a suggested tag, anonymised sample values, recognizer info, and a PENDING/REVIEWED… status. The effect in ingestion: a recognizer is **skipped for a column** if that column's entity link is in the recognizer's `exceptionList`. Feedback becomes a per-column, per-recognizer suppression.

**Conflict resolution.**
- Group by classification, and drop tags below `minimumConfidence`.
- If mutually exclusive, pick one winner:
  - `highest_confidence`: max of (score, column-name-matched, priority). Column-name evidence breaks ties between sibling tags that share a content recognizer, such as DateTime vs BirthDate.
  - `highest_priority`: (priority, score).
  - `most_specific`: (FQN depth, score).
- Otherwise keep all.
- Before analysis, skip tags already applied, and skip whole mutually-exclusive classifications that already have a tag (manual tags win).
- Labels are written as `LabelType.Generated`.

**Legacy column-name scanner.** English regexes split into "sensitive" (password, ssn, credit card, bank account, email, user/person names) and "non-sensitive" PII (dob, gender, nationality, address parts, phone), with confidence 1.

**Languages / Vietnamese.** spaCy model map per language (`*_core_news_md`). `vi` exists in the language enum but **is not mapped, so it falls back to `xx_ent_wiki_sm`** (small multilingual NER). There is no Vietnamese ID checksum, no Vietnamese column-name vocabulary, and the column-name regexes are English only.

### 4.2 Clever
- Max-not-mean with NER corroboration: high recall without letting one spaCy hallucination tag a column.
- The cardinality gate.
- The ALL-CAPS retitle pass.
- Column name as a tie-breaker in conflict resolution.
- Per-column recognizer exceptions as the feedback loop.
- Storing which recognizer and pattern fired, which makes review explainable.

### 4.3 Breaks
- Vietnamese is effectively unsupported (multilingual small model, English name regexes).
- Name-only scoring at confidence 1 in the legacy scanner over-tags (`username` → sensitive).
- The exception list is per recognizer and per column, so it does not generalise: the same false positive on 200 `company_name` columns needs 200 feedbacks.

### 4.4 Clean-room design for us (`backend/libs/pii`, checksum packs: CCCD with province codes, CMND, Luhn cards, IBAN, SSN, phone, email)

We are stronger on validated identifiers. Take the *decision logic* (S–M):

1. **Two-tier threshold** in `column_label` / `personal_share` (today `PERSONAL_SHARE = 0.5` in `schema_scans.py`):
   - **checksum-validated types** (CCCD, card+Luhn, IBAN, SSN with area rules): one valid hit in the sample is enough (contaminating);
   - **weak types** (phone, email, free-text names): require share ≥ 0.2 **and** ≥ 2 distinct matches.
   - Content analysis is skipped when unique/total < 0.01.
2. **Name and content fusion:**
   - `score = max(content, name)`; the name wins ties;
   - store `evidence = {target, pack, rule, matches_distinct, share}` in `catalog.column_meta`, so stewards see why.
   - Our name vocabulary (`_PII_NAME_PARTS`, `_AMBIGUOUS_PARTS` with `ten`, `khoan`, `thue`, and `_PERSON_PARTS`) stays ours, with accent-folded matching.
3. **Feedback table** `catalog.pii_feedback(column_id, pii_type, kind, reason, created_by)`, with kinds modelled on the Apache-2.0 enum. It suppresses that (column, type) on later scans. **Generalise beyond OM:** if ≥ 3 feedbacks share a normalised column-name token and type, propose (HITL) a data-space-wide name exception.
4. **Never overwrite manual labels.** Mutually exclusive `pii_label`: one winner by (score, name_matched, type priority). `_PRIORITY` already exists.
5. Vietnamese names: add an ALL-CAPS → title-case pass before the person-name heuristic. Do not adopt spaCy `xx` for Vietnamese.

---

## 5. dbt and semantic-layer import

### 5.1 How it works

**dbt.** Inputs are `manifest.json` (required), `catalog.json` and `run_results.json` (optional), from local, HTTP, S3/GCS/Azure, or dbt Cloud. Each model, seed, snapshot, or source is matched to an existing catalogue table by FQN search (with a db-name mapping for catalogs). Then:
- **Descriptions:** table and column descriptions are patched from dbt. `force` (overwrite existing) only when the node is **not a source** *and* `dbtUpdateDescriptions` is on; otherwise dbt fills only empty descriptions.
- **Owners:** precedence is `meta.openmetadata.owner` > `meta.owner` > catalog `metadata.owner`. A string or list is resolved by name, then by email. Owners are replaced only for non-source nodes with `dbtUpdateOwners` set.
- **Tags:** dbt tags (filterable) become a dbt tag classification. Domain, data products, and custom properties come from `meta.openmetadata.*`, validated against the defined custom property types.
- **Lineage:** `depends_on` upstream nodes → table edges (source `DbtLineage`, with compiled SQL on the edge, optional `overrideLineage`). The compiled SQL is also parsed with the query-lineage parser for column lineage. Exposures → dashboard/ML edges. An upstream that does not resolve is warned **once** per upstream, not per model.
- **Tests:** dbt tests become test definitions and test cases. Run results become test results; statuses are mapped to Success/Failed/Aborted, timestamps come from `completed_at` with fallbacks, and source freshness results become a freshness test result.
- **Metrics (MetricFlow):** each metric type gets an expression and related metrics:
  - simple → the measure name, or the inline `expr` on dbt ≥ 1.12, where the aggregation lives in `metric_aggregation_params.agg`;
  - derived → `expr`, plus sub-metrics as related;
  - ratio → "numerator / denominator", both related;
  - cumulative → the measure plus "over N granularity";
  - conversion.
  - Dimensions and measures are pulled from the semantic models, filters and tags carried, and metric → table and metric → metric lineage built, with column-level lineage where the measure's column can be found.

**Snowflake semantic views / Databricks metric views → Metric entities.** Shared rules (`semantic_metrics.py`):
- **Stable name:** service + a digest of the ordered identity (db, schema, view, [table], measure), after unquoting each part (decoding `""`), so quoted and raw catalogue values hash the same.
- **Type inference:** the leading function name of the expression maps to SUM, COUNT (incl. COUNT_IF and APPROX_COUNT_DISTINCT), AVERAGE (AVG, MEAN), MIN, MAX, MEDIAN, MODE, STDDEV*, or VARIANCE/VAR_*; anything else is OTHER. The aggregation name is reported only when the type is known (so `MEASURE(a)/MEASURE(b)` is not "MEASURE").
- **Dimension type:** TIME if the declared type contains DATE, TIME, or TIMESTAMP; CATEGORICAL otherwise; none if no type.
- **Descriptions:** the comment, plus "Synonyms: …" appended, because synonyms have no field.
- **Snowflake:** queries `INFORMATION_SCHEMA.SEMANTIC_VIEWS`, then one query **per schema** (not per view) over SEMANTIC_TABLES/DIMENSIONS/FACTS/METRICS (name, data type, expression, comment, synonyms).
- **Databricks:** a metric view is recognised by its stored text parsing as YAML whose mapping has `measures` (a SQL view never does). Once it is recognised, a validation failure raises instead of silently returning "not a metric view". Joins and aliases are resolved to source tables for column lineage, and units of measurement are mapped.

### 5.2 Clever
- "Non-source nodes may overwrite, sources only fill blanks."
- Owner precedence with email fallback.
- Warning once per unresolved upstream.
- Digest-based stable metric names robust to quoting.
- The YAML-with-measures discriminator.
- Raising on "is one, but unreadable".

### 5.3 Breaks
- Synonyms are buried in descriptions, so they are lost for search and agents.
- Type inference by leading token misreads `COALESCE(SUM(x),0)` and `SUM(a)/SUM(b)` (OTHER, or SUM for a ratio).
- Ratio expressions are textual "a / b" with no NULLIF.
- Owners are resolved by name and may hit the wrong team on a name collision.

### 5.4 Clean-room design for us

We already have `api/modules/context/semantic_yaml.py` (MetricFlow-shaped import/export; relationships land `pending_review`, metrics land as draft versions). Extend (M–L):
- **New `backend/services/api/modules/context/dbt_import.py`** (M), run via a `make dbt-import` target in the api pod (CLAUDE.md §5: a make target, no shell script).
  - Parse the manifest with plain json; no dbt-core dependency.
  - Map models to `catalog.dataset` by (database, schema, alias|name).
  - Descriptions: OM's rule (non-source may overwrite only with a flag, otherwise fill blanks), written as **proposals** (`inbox.proposal`) unless the org enables auto-apply.
  - Owners: by email first (deterministic), then by exact name, never by fuzzy match.
  - `depends_on` → `pipeline.lineage_edge via='dbt'`.
  - dbt tests map onto our kinds: `not_null`→not_null, `unique`→unique, `accepted_values`→in_list, `relationships`→`context.relationship(origin='declared', evidence='dbt')`, `dbt_utils.expression_is_true`/singular tests→custom_sql, `source freshness`→freshness. Rules start in draft.
  - `run_results` → quality observations.
  - MetricFlow metrics → the existing `semantic_yaml` importer path (reuse, do not fork).
- **Semantic views/metric views (M):** add a reader in `source_meta.py` for Snowflake `INFORMATION_SCHEMA.SEMANTIC_*` and Databricks `METRIC_VIEW` YAML. Map them to the same intermediate model `semantic_yaml` consumes.
  - Write synonyms to `context.synonym`, which we have and OM lacks.
  - Infer metric type with **sqlglot**: walk the AST and take the outermost aggregate under any COALESCE/CAST; if there are ≥ 2 aggregates combined arithmetically, the metric is derived/ratio.
  - Stable identity: our `metric.code` + a digest of (source_id, db, schema, view, measure) after identifier normalisation.

---

## 6. Connector framework (design ideas)

### 6.1 How it works
- **Steps:** `Source` (iterator) → zero or more `Processor` (record→record) → `Sink` (record→ack), or `Stage` (record→local files) → `BulkSink` (files→server). Every step returns `Either(left=StackTraceError, right=value)`. Errors are values, counted into a per-step `Status` (records, updated_records, warnings, filtered with reason, failures with truncated stack traces).
- **Success threshold:** the workflow succeeds if the success % ≥ `successThreshold` (80 for profiler workflows). It reports per-step success, and a final mean.
- **Topology runner:** a declarative tree, e.g. service → database → schema → table → (stored procedures). Each `TopologyNode` has a `producer` method name, `stages` (entity type, processor method, context key, `cache_all`/`store_all_in_context`, `clear_context`, `consumer`, `overwrite`, `must_return`), `children`, `post_process` hooks, and `threads`. Threaded nodes split producer output into `ceil(n/threads)` chunks; results flow back through a queue. The context tracks the current FQN path per thread.
- **sourceHash:** each create request gets a deterministic hash. Lists are sorted (columns by ordinal, then name; tags by FQN; constraints by type and columns), volatile fields are dropped (`href`, `deleted`, `inherited`, certification dates, the hash itself), and DDL whitespace is collapsed. The server's bulk endpoint compares the hash and **skips unchanged entities**. The connector no longer fetches existing entities to diff.
- **Incremental extraction:** start = the last successful run's start inside a `lookbackDays` window (default 7), minus `safetyMarginDays` (default 1). If there is no success in the window, it does a full run. Sources such as BigQuery and Redshift then list only tables changed since the start (BigQuery from Cloud Logging, with retries).
- **Progress:** declared totals per node type and a progress tracker per entity type.

### 6.2 Clever and broken
- Clever: errors-as-values with a success threshold (a single bad table does not kill a 10 000-table run); hash-based skip moved to the server; the safety margin on incremental watermarks.
- Broken: SIGALRM timeouts (§1.3), and threads that share a context object through thread-local keys (complex).

### 6.3 What we take
- **Fix `schema_hash` (S).** Today it is `sha256(repr(columns))` in `schema_scans.py:702`, so it is sensitive to dict order, repr formatting and volatile fields. Replace it with a canonical hash: sort columns by ordinal, include only (name, normalised type, nullable, ordinal), and use JSON with `sort_keys`. Then `additive` vs `breaking` diffs stop firing on noise. Needs a one-time recompute migration that does not create new versions.
- **Errors as values in scans (S):** `scan_run` gets `tables_ok`, `tables_failed`, `failures jsonb` (truncated), and a configurable success threshold, instead of the whole scan flipping to `failed` on one exception (today's `except: run.status='failed'; raise`).
- **Watermarks (S)** for query logs and DML snapshots: `catalog.source_watermark(source_id, stream, last_success_at)`, start = last − margin.
- Skip the topology runner. Our event-per-step worker plus outbox already composes better.

---

## 7. Ranked: 8 things to build

| # | What | Where | Effort | Why first |
| --- | --- | --- | --- | --- |
| 1 | Canonical `schema_hash` + errors-as-values scan status | `worker_agent/schema_scans.py`, migration | S | Stops false breaking/additive diffs; prerequisite for trusting version history |
| 2 | Profile push-down rewrite: one batched aggregate per table, catalogue row estimate (handle `reltuples<0`), TABLESAMPLE above 100k rows, overflow retry, percentiles in one pass | `schema_scans.py`, `source_meta.py`, `catalog.column_profile` | M | Today it does 2 full scans per column; this is the scaling cliff on real warehouses |
| 3 | PII decision logic: two-tier threshold (checksum = contaminating, weak = share + distinct), cardinality gate, evidence stored, ALL-CAPS name pass | `libs/pii`, `schema_scans.py` | S | Cheap; directly improves a security control we already ship |
| 4 | sqlglot query analysis lib: read/write sets, column lineage with schema-aware `*`, join extraction (ON/USING), literal masking with ordinal preservation, fingerprint, real process timeout | `libs/sql_guard/lineage.py` | M | Foundation for 5 and 6; also useful for our own agent SQL (lineage of answers) |
| 5 | Postgres query-log reader with `pg_stat_statements` snapshot deltas + self-tag exclusion; usage daily table with PERCENT_RANK 1/7/30 | `worker_agent/query_log.py`, `catalog.query_fingerprint`, `catalog.dataset_usage_daily` | M | Usage ranks feed retrieval (which tables to show the LLM first) and fix OM's main Postgres flaw for us |
| 6 | Query-log joins → inferred relationships (`evidence='query_log'`, `join_count`, overlap checked after), and query lineage → `pipeline.lineage_edge via='query'` with temp-table collapse | `query_log.py`, `context.relationship`, `pipeline.lineage_edge` | M | Real joins people run are the best join evidence; still `pending_review` |
| 7 | DQ: tolerance, dimensional rules with smooth impact score and Others row, aborted status, masked 50-row failed samples, then robust dynamic bounds (median ± 3·MAD, HITL opt-in) | `worker_agent/quality.py`, `ingest.quality_rule`, `ingest.quality_observation` | M | Turns 7 rule kinds into explainable, low-noise monitoring |
| 8 | dbt manifest import (descriptions as proposals, owners by email, tests → our 7 kinds, depends_on → lineage, metrics → existing semantic_yaml path) + Snowflake/Databricks semantic views with synonyms → `context.synonym` | `api/modules/context/dbt_import.py`, `source_meta.py` | L | Big onboarding win for dbt shops, but needs 4 and the semantic importer stable first |

Smaller follow-ons: Postgres DML deltas from `pg_stat_user_tables` for freshness and volume (S, ride along with 2), and a per-column PII feedback table with name-token generalisation (S, after 3).

---

## 8. Explicitly NOT to reuse

1. **Any code from `ingestion/`.** That covers every function, class, mixin and test. It is Collate Community License 1.0, which bars a competing SaaS. Specifically:
   - the `LineageParser`/masker implementation;
   - the topology runner;
   - the profiler metric classes and `@compiles` maps;
   - the validators and the impact-score module (we use our own smooth formula);
   - the PII `TagAnalyzer`, `TagScorer`, `ConflictResolver` and Presidio patches;
   - the dbt and semantic-metric modules;
   - `source_hash.py`.
2. **OM's SQL templates for log and system tables** (the Snowflake/BigQuery/Redshift/MSSQL/MySQL/Postgres history queries, the Redshift `stl_*` reconstruction, the Snowflake procedure correlation). We write ours from the vendor documentation. Only the *facts* (which system view holds what) are reused.
3. **OM's regex lists:** column-name PII regexes, the Snowflake DML target regex, `clean_raw_query` patterns. Our own vocabularies already exist and are Vietnamese-aware.
4. **Docstring and comment prose** from those files: no copying into our comments or docs.
5. **`collate-sqllineage` and the `openmetadata-ingestion` package as dependencies.** They are not needed, since sqlglot covers it, and depending on them would entangle licensing.
6. From the Apache-2.0 parts (spec JSON, Java service), reuse is permitted with attribution, but we should still model our own tables. The usage percent-rank is a standard `PERCENT_RANK` and needs no copying. The `recognizerFeedback` enums may inform our `pii_feedback.kind` values, credited in the migration comment.
7. **Dynamic-assertion learning is not in OSS** (Collate closed code). Do not try to obtain it. §3.4(c) is our independent design.
