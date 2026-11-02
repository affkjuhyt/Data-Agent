# Airbyte's restrictively licensed parts, read clean-room; and a licence audit of everything researched

> Status (03/11/2026): F1–F8 are fixed in the commit that adds this file (AGPL and NC blocks replaced by our own summaries, ELv2 YAML removed, licence headers and corrections, code comments and prompt labels). F10, a THIRD-PARTY-NOTICES file, is due with the first on-prem delivery.

Date 2026-11-03. Not legal advice: this is an engineering reading of licence texts. Where the
answer decides whether something ships, ask counsel.

**What was read, at which commit**

| Clone | Commit | Licence file read |
| --- | --- | --- |
| `research3/airbyte/pyairbyte` | `1182d56` (2026-10-30) | `LICENSE.md` = ELv2 |
| `research3/airbyte/cdk` | `fd9e6c2` (2026-10-30) | `LICENSE.txt` = MIT, `pyproject.toml` `license = "MIT"` |
| `research3/airbyte/protocol` | shallow | `LICENSE` = MIT |
| `research3/airbyte/main` | `d9cd7e7` (2026-11-02) | root `LICENSE` = ELv2. For this report the sparse checkout also took `source-{postgres,mysql,mssql,google-sheets,salesforce,hubspot,shopify,stripe,google-analytics-data-api}`. Each one's `metadata.yaml` says `license: ELv2` (or `Elv2`) |
| `research3/airbyte/raw/oss_registry.json` | registry snapshot | licence and support level for each of the 592 sources and 54 destinations |
| `research2/repos/*`, `research/DataAgent`, `research3/openmetadata/om-sparse` | as in the earlier notes | see Part B |

**Clean-room rule for this document.** Nothing below is copied from an ELv2, AGPL, Collate or
CC BY-NC source. Behaviour is described in my own words. The pseudo-code is mine. Only short
identifiers are quoted (class names, enum values, config keys), so a reader can find the place.

---

# Part A: Airbyte's ELv2 parts

## A.0 The licence boundary, stated once

ELv2 (PyAirbyte, all connectors in `airbyte-integrations/connectors/` except 33 MIT sources and
15 MIT destinations) grants use, copy, distribute and derivative works. It has three limits:

1. no providing the software to third parties as a hosted or managed service that gives access to
   a substantial set of its features;
2. no circumventing or removing licence-key functionality;
3. no removing or obscuring licence and copyright notices.

The registry (`oss_registry.json`) counts the sources: **553 ELv2** (63 of them `certified`; the existing airbyte.md says 549 because it missed 4 spelled `Elv2`),
**33 MIT**, and 6 `Airbyte Enterprise` (`source-db2-enterprise`, `-netsuite-enterprise`,
`-oracle-enterprise`, `-sap-hana-enterprise`, `-sharepoint-enterprise`, `-sharepoint-lists-enterprise`),
which are commercial and not usable at all without a contract. Every connector this brief named is
ELv2: Postgres, MySQL, MSSQL, Google Sheets, Salesforce, HubSpot, Shopify, Stripe and GA4.

The **MIT** parts carry the reusable value. The CDK holds the declarative framework, the
component schema `declarative_component_schema.yaml` (140 component types), the concurrent runtime,
the error model, the **Connector Builder backend** (`airbyte_cdk/connector_builder/`) and the
**manifest server** (`airbyte_cdk/manifest_server/`). The protocol models are MIT too. The upshot
for us: we may run the MIT CDK runtime with **our own** manifests, and we may write our own
executor and cache layer after learning how PyAirbyte does it. We may not host Airbyte's ELv2
connectors or PyAirbyte inside our SaaS as the thing customers use to sync. Bundling them on-prem
also needs a decision (see B.3).

## A.1 PyAirbyte, behaviour by behaviour

### A.1.1 Locating and installing a connector

The factory (`_executors/util.py: get_connector_executor`) accepts at most one explicit install
hint: a local executable, a Docker image, a pip URL or Python, a manifest, or "no executor"
(spec-only). Two hints at once are an input error. With no hint it consults the **registry
metadata** for the connector and picks in this order:

1. an executable of that name on `PATH`;
2. the registry's `default_install_type`: YAML gives the manifest path, PYTHON gives a PyPI
   package (pinned when a version is asked), anything else gives Docker.

The registry is fetched over the network. When it cannot be reached the call fails, unless an
offline flag is set; a custom registry URL can be configured. The executors are:

- **VenvExecutor**: one virtualenv per connector under an install root. It pip-installs the
  package, then checks the installed version against the target and reinstalls on mismatch
  (`ensure_installation(auto_fix=True)`). It shells out to the connector's CLI entry point.
- **DockerExecutor**: `docker run --rm -i`, a per-connector local directory mounted as `/local`
  and the host temp dir mounted for config and catalog files, optionally on the host network.
  Version means image tag, `latest` when none is given.
- **DeclarativeExecutor**: the **in-process** path. It holds a manifest dict, loaded from a path
  or fetched from the registry's manifest URL for a version, plus an optional sidecar of custom
  Python components. It injects the components into the connector config under reserved keys
  with an MD5 checksum. On each command it reads `--config` itself, builds a fresh
  concurrent declarative source from manifest plus config, and runs the CDK entrypoint on it. It
  rebuilds the source for every call because a finished concurrent source does not tolerate a
  second read. Installing it is a no-op. Its "version" is the manifest's `version` field.
- **NoOpExecutor**: spec and docs from the registry, no running.

*What to learn:* the executor is a strategy that hides one protocol (spec / check / discover /
read over line-delimited JSON messages), and the declarative case needs no subprocess at all.
*What not to copy:* runtime pip and Docker pulls from the public internet. They break air-gapped
installs and image pinning. The custom-components sidecar is arbitrary code execution, and the CDK
itself gates it behind `AIRBYTE_ENABLE_UNSAFE_CODE`.

### A.1.2 Reading: catalog, selection, sync modes

A `Source` discovers the catalog once and caches it. The caller selects streams (or `*`) and may
override the primary key or cursor per stream. The configured catalog picks each stream's sync
mode: incremental when the stream supports it and a full refresh is not forced, full refresh
otherwise. `get_records(stream)` is a lazy iterator with no cache. `read(cache, write_strategy,
force_full_refresh)` streams messages into a cache:

```
read(cache, streams, strategy=AUTO, full_refresh=False):
    state_in  = None if full_refresh else cache.state_for(source_name)
    state_out = cache.state_writer(source_name)
    catalog   = configure(selected_streams, full_refresh)
    validate(config) unless skipped
    for msg in connector.read(config, catalog, state_in):
        RECORD -> normalise keys, drop fields not in the stream schema, buffer to a local file batch
        STATE  -> keep as pending for its stream (or for GLOBAL)
        TRACE stream_status=SUCCEEDED -> finalise that stream now
    finalise every stream; checkpoint the cache
```

**Finalising a stream** is the important part. The buffered files load into a **temp table**.
The temp table goes into the final table with one write method, and only then is the stream's
last pending state message persisted. So **state never runs ahead of data**. The write method
comes from the strategy: an explicit `merge` / `append` / `replace`, or `auto`, which means merge
when the stream has a primary key, append when it only has a cursor, replace otherwise. Merge with
no primary key is refused. `replace` without a forced full refresh warns about data loss. Replace
swaps tables by renaming (old to `_deleteme`, temp to final, drop old). Merge uses native `MERGE`
where the engine has it and an emulated delete-then-insert otherwise.

### A.1.3 Caches and state

The caches are DuckDB (default, a local file), Postgres, Snowflake, BigQuery and MotherDuck. They
sit on SQLAlchemy and share one processor. DuckDB loads batch files with its native JSON reader
rather than row inserts. **State and the catalog live in the cache database itself**, in internal
tables keyed by (source name, stream name). One state table serves caches, another destinations;
the latter is also keyed by destination name. So a second `read()` against the same cache is
incremental with no external store.

### A.1.4 Schema evolution

PyAirbyte only adds. Before append or merge it compares the stream's JSON-schema columns with the
table's. It refreshes its reflected-table cache only when something looks missing, and issues
`ALTER TABLE … ADD COLUMN` for each column still absent. Type changes and removed columns are not
handled: extra record fields are pruned to the declared schema, and a replace rebuilds the table
anyway. Top-level keys are normalised (lower case, separators to `_`) with a lookup back to the
display case. Nested keys are left alone.

### A.1.5 Secrets

There is a chain of secret managers: environment variables, `.env`, Colab, an interactive prompt,
Google Secret Manager, or a custom one registered at runtime. A config value can be a reference
(prefix `secret_reference::NAME`), and hydration walks the config replacing references
recursively. There is also a **hardcoded-secret detector**: it takes the secret field names from
the connector spec (or a global mask list fetched from Airbyte) and reports every path in a config
where such a field holds a literal value rather than a reference. Secret values are wrapped in a
string subclass whose `repr` is masked.

### A.1.6 Streaming out

A cached stream is a dataset that can be iterated, turned into pandas or Arrow, or queried with
SQL against the cache engine. The MCP server (`mcp/local.py`) exposes the same set to an agent:
validate config, list config secrets, list streams, a stream's JSON schema, read N records,
previews of each stream, sync to cache, list cached streams, and run a SQL query. The SQL query
passes through a simple read-only check first.

### A.1.7 What we take from PyAirbyte (clean-room)

Ideas, re-implemented: (a) state committed after the data of the same stream, never before;
(b) `auto` write-method resolution; (c) state and catalog stored next to the data, in our case in
the `ingest` schema; (d) add-only schema evolution as the safe default; (e) secret references plus
a detector for literal secrets, using each source spec's secret flags; (f) a per-stream preview of
N records for onboarding; (g) an executor strategy whose declarative branch runs in-process. None
of this needs PyAirbyte as a dependency. The in-process declarative branch can call the
**MIT CDK** directly: build the declarative source from a manifest and a config, then run its read
generator.

## A.2 Certified ELv2 connectors: the patterns

### A.2.1 Database sources (Postgres, MySQL, SQL Server)

All three are now Kotlin, on Airbyte's "bulk" JDBC toolkit, with Debezium for CDC. The patterns:

- **Three update methods per source.** (1) A *user-defined cursor*: a column chosen per stream,
  read with `cursor > last AND cursor <= upper_bound`. The upper bound is sampled at the start of
  the sync (the current max), so rows written during the sync wait for the next run rather than
  being half-read. (2) Postgres only, *xmin*: the system transaction id is the cursor, with
  wraparound handling, no user column needed. (3) *CDC*: Postgres logical replication through a
  named **replication slot** and **publication**; MySQL binlog with server timezone and GTID
  position; SQL Server CDC tables. CDC rows get meta columns (LSN or binlog position, updated-at,
  deleted-at) so soft deletes are visible.
- **Initial snapshot before CDC**, resumable. Postgres splits a table by **physical row location
  (CTID) ranges** sized from the relation's block count, and uses TID range scans on servers that
  support them. It records the table's **filenode**: if the filenode changed between runs (VACUUM
  FULL, table rewrite), the CTID positions are meaningless and the snapshot restarts. MySQL and SQL
  Server split by primary-key ranges. An `initial_load_timeout_hours` bounds the snapshot. If the
  saved CDC position is no longer available (the binlog was purged, the slot was dropped),
  `invalid_cdc_cursor_position_behavior` chooses between failing the sync and re-syncing.
- **Checkpoint cadence** is a target interval (`checkpoint_target_interval_seconds`), not every
  N rows. Concurrency is bounded by `max_db_connections` / `concurrency`.
- **Privilege check** (`check_privileges`): during discovery each table and column is probed for
  SELECT, so streams the account cannot read are left out instead of failing mid-sync.
- **Exclude today's data** (SQL Server): for a DATE cursor the cutoff is today's date; for a
  timestamp cursor it is today at 00:00. This guards daily-partitioned facts against a partial
  day.
- **LSN commit behaviour and heartbeat** (Postgres): whether the slot's position is acknowledged
  while records are read or after the destination confirms, and an optional heartbeat statement so
  a quiet database still advances the slot (otherwise WAL accumulates).
- **Type mapping is one class per engine type** (money, hstore, timetz, bytea, bit-as-boolean…),
  each declaring its JSON type and how to read it. Connection rules cover TLS modes, SSH tunnel
  and, for Postgres and SQL Server, Entra ID service-principal auth.

### A.2.2 Google Sheets (manifest-only, with a components sidecar)

- **Dynamic streams**: one stream per sheet tab, resolved at discovery from the spreadsheet's
  metadata (`DynamicDeclarativeStream` + `HttpComponentsResolver`). The **schema is the header
  row**, so it is dynamic per tab, and an option converts header names into SQL-safe identifiers.
- Rows come in **row-range batches** (a configurable `batch_size`). A range router slices the
  sheet, and empty rows are skipped by checking only the columns that have headers.
- Auth is either OAuth (client id, secret, refresh token) or a **service-account JWT**, chosen by a
  selective authenticator from the config's auth type.
- Rate limits: a moving-window call budget, plus error filters that map 429 to back-off with
  exponential retries, and 403 or 404 to configuration errors that tell the user to share the
  sheet.

### A.2.3 Salesforce (Python CDK)

- **Schema by describe**: the object list comes from the org's global describe, and each object's
  fields come from per-object describe, converted to JSON schema. The **cursor is picked by field
  presence**: `SystemModstamp` if present, else `LastModifiedDate`, else full refresh only. The
  primary key is `Id`.
- **Two read paths**. The *Bulk API* (an async job per slice: create, poll, download CSV results,
  a date-window slicer) for most objects. *REST/SOQL* for the objects the bulk API refuses (lists of
  unsupported and query-restricted objects are kept in code), and when the field list is too long
  for one URL: then the select list is **chunked by character length** and the chunks are re-joined
  by `Id`.
- **Token provider with proactive refresh**. After a refresh-token failure the login is marked as
  permanently failed, so the connector stops retrying. Error handling tells login errors, invalid
  field errors (the field is dropped) and bulk-job-creation errors apart and assigns each a
  resolution.

### A.2.4 HubSpot (manifest-only, ~9.6k lines, plus components)

- CRM objects are read through the **search endpoint** with a `lastmodifieddate` cursor. Search
  stops at **10,000 results per query**, so a component restarts the query from the last id seen.
  It is keyset pagination layered over a capped API.
- **Property chunking**: an object can have thousands of custom properties. The property list is
  fetched from a properties endpoint (`PropertiesFromEndpoint`) and split into requests under a
  character limit (`PropertyChunking`, 15,000 characters). The partial records are merged by
  primary key.
- Many **state migrations** (`CustomStateMigration`), because the connector moved from Python to
  YAML and from v1 to v3 APIs. Old state shapes are converted on read, not rejected.
- Back-off reads the server's wait header (`WaitTimeFromHeader`).

### A.2.5 Shopify (Python CDK, GraphQL bulk operations)

- Most streams use **GraphQL bulk operations**: submit a bulk query for a date slice, poll its
  status, download the JSONL result, and reassemble parent and child objects from the flat lines.
- An **adaptive slice size**: the job manager widens the date window when jobs finish fast and
  narrows it when they run long. A long-running job is cancelled and retried on a smaller window.
  When the job type allows it, the manager **checkpoints mid-job** by cancelling at a threshold
  and resuming from the last cursor collected. It detects a checkpoint that collides with the
  previous one so it cannot loop.

### A.2.6 Stripe (manifest-only)

- **Two streams per entity**, behind a `StateDelegatingStream`: with no state, a full refresh uses
  the entity's list endpoint; with state, an incremental read uses the **events** endpoint, with a
  cursor on the event time. Event payloads are flattened to the object, and a `.deleted` event
  sets an `is_deleted` flag. Events are retained for only 30 days, so the incremental start is
  clamped to "now minus 30 days" (an `api_retention_period`). Past that window, state is treated as
  stale and the stream falls back to a full refresh.
- **Call budget** derived from the key: test-mode keys get the sandbox rate and live keys the live
  rate, unless the config overrides it. A stricter policy applies to the file endpoints. 429 is
  declared as the rate-limit signal.
- Cursor pagination with `starting_after`; date slices of configurable width plus a lookback
  window.

### A.2.7 Google Analytics 4 (manifest-only)

- **Custom reports** are user-defined dimension and metric sets, and each becomes a dynamic stream.
  Its schema is generated from the chosen dimensions and metrics with a type map.
- **Tier-aware budget**: the property tier (standard or 360) selected in config sets the local
  request rate, so the connector stays under Google's per-property quota instead of discovering it
  through 429s. Offset pagination; date-range slices.

### A.2.8 The generic HTTP pattern (from the MIT CDK, usable as is)

The declarative framework has a fixed vocabulary that covers every API above:

- *Auth*: API key, bearer, basic, OAuth (refresh), JWT, session token, selective (pick by config).
- *Pagination*: cursor, offset-increment, page-increment, none; a stop condition.
- *Incremental*: a datetime cursor with step, granularity, lookback window, min and max clamps,
  and request options that inject start and end into params or body.
- *Partitioning*: list router and substream (parent to child) router.
- *Record pipeline*: extractor, filter, transformations (add, remove, flatten fields).
- *Errors*: an `HttpResponseFilter` maps status, message or predicate to a `ResponseAction`. The
  MIT enum is SUCCESS, RETRY, FAIL, IGNORE, RATE_LIMITED, RESET_PAGINATION,
  REFRESH_TOKEN_THEN_RETRY, REDUCE_PAGE_SIZE, SPLIT_REQUEST_WINDOW. A failure carries a
  `FailureType`: config_error (the user must fix something), system_error, or transient_error.
  Back-off is constant, exponential or read from a header.
- *Budget*: a moving-window call-rate policy with request matchers.

**Patterns worth keeping, across connectors:**

| Concern | Pattern |
| --- | --- |
| Auth | auth chosen by config discriminator; proactive token refresh; "permanently failed" latch after a refresh-token rejection |
| Pagination | cursor or keyset over offset; restart a capped search from the last id (HubSpot); chunked field lists merged by PK (Salesforce, HubSpot) |
| Incremental | cursor upper bound fixed at sync start; lookback window; clamp to the API's retention (Stripe); exclude today's partial day; state migrations instead of resets |
| Rate limits | local budget from known quotas (tier, key type) before 429s; honour server wait headers; adaptive slice size (Shopify) |
| Schema | discovered from the API itself (describe, header row, properties endpoint, report definition); privilege-filtered |
| Errors | each failure classified as config, transient or system, with a user-facing message; resolutions that change the request (smaller page, split window) rather than only retrying |

## A.3 Connector Builder, manifest server, AI assistant

- **Manifest server** (MIT, `airbyte_cdk/manifest_server/`): a FastAPI app behind a JWT check with
  `POST /manifest/test_read`, `/check`, `/discover`, `/resolve` (expand `$ref`s and parameters),
  `/full_resolve` (also expand the dynamic streams), plus `/capabilities` and `/health`. Test read
  takes a manifest, a config, one stream name, an optional state and optional custom-component
  code, with bounded limits: records 1–5000 (default 100), pages 1–20 (default 5), slices 1–20
  (default 5). It forces **concurrency 1** so the returned HTTP exchanges stay in order. Error
  messages are **secret-filtered** before they are returned. Custom code runs only when the server
  advertises the `custom_code_execution` capability, which the unsafe-code env var turns on.
- **Test reader** (MIT, `connector_builder/test_reader/`): it groups the message stream into
  slices, then pages, then each page's request, response and records. It also infers a schema
  from the records it saw and reports the latest config update (for example a refreshed token).
  The Builder UI renders exactly that tree.
- **Versioning**: the manifest declares the CDK version it was written for. `manifest_migrations/`
  holds ordered, version-ranged migrations, each with should-migrate, migrate and validate steps,
  that upgrade old manifests on load. Published Builder connectors are versioned per workspace; a
  "contribute" path opens a PR to the catalog.
- **AI assistant**: Cloud-only and closed (`ai-assist.md`, `products: cloud`). From the docs only:
  the user gives an API documentation URL or an OpenAPI URL. The assistant crawls it and pre-fills
  the base URL, auth, pagination, primary key, record selector, request options, incremental
  cursor and the list of streams, then makes suggestions field by field. A human reviews and
  test-reads the result. No code exists to study, and none is needed: the loop is
  *draft from docs, then test-read, then a human fixes it*.

## A.4 Mapping to our modules: clean-room designs

**Where we stand** (graphify, then the files read). `worker_ingest/worker.py` handles only
`mode == "full"`. It refuses incremental and CDC with "need cursor adapters". It reads
`SELECT * FROM schema.table` **entirely into memory** (asyncpg `fetch`, or the driver's
`fetchall`) and materialises it through `da_materialize_upload` as one JSONB payload. Timestamps
become text. `ingest.IngestionJob` already has `mode`, `cursor_column` and `cursor_value`;
`JobRun` has `cursor_from` and `cursor_to`; the API rejects an incremental job without a cursor
column. `libs/source_drivers` gives MySQL, SQL Server and ClickHouse fail-closed TLS and returns
(columns, rows). `providers/registry.py` registers only `RelationalProvider`. `catalog.Source.kind`
already lists `rest_api`, `kafka_cdc`, `sharepoint` and `sftp`, but nothing reads them.

| # | Design (ours) | Module | Effort |
| --- | --- | --- | --- |
| 1 | **Cursor incremental for DB sources.** At run start, sample `upper = max(cursor)`. Read `cursor > job.cursor_value AND cursor <= upper` (or `>=` when a lookback is set) in keyset pages ordered by (cursor, PK). Write the batch and set `JobRun.cursor_from/to` and `IngestionJob.cursor_value = upper` **in the same transaction**: state never ahead of data. With a PK, merge into the workspace table, else append (our `auto`). An optional "exclude today" cutoff for date cursors. | worker_ingest + source_drivers | M |
| 2 | **Streaming read.** Replace fetch-all with a server-side cursor or keyset pages (asyncpg `cursor()`, a PyMySQL SSCursor, pyodbc `fetchmany`). Materialise per page into a staging table, then swap or merge. Bound memory and give each run a row ceiling. | source_drivers, worker_ingest | M |
| 3 | **Discovery as a catalog.** A driver function that returns tables, columns with engine type, PK, candidate cursor columns (timestamp or monotonic), and a SELECT-privilege flag per table. It feeds the onboarding UI and cursor validation (reject a cursor column that does not exist or is not orderable). | source_drivers | S |
| 4 | **Add-only schema evolution.** Before a merge or append, compare source columns with the workspace table. Add missing columns; never drop or retype them. Record a `catalog` change event (`change_kind`) so a human sees drift. A type conflict fails the run with a config-class error. | worker_ingest + catalog | S |
| 5 | **Failure classes.** Every run error is `config` (credentials, privilege, missing table: no retry, tell the user), `transient` (timeout, 429, connection reset: back-off retry), or `system` (bug: page). Store the class on `JobRun`, and use `consecutive_failures` only for transient errors. | worker_ingest | S |
| 6 | **REST sources with our own manifests on the MIT CDK.** `kind = rest_api` gets a published manifest per connector version, stored in a table with a version and a checksum, written by us in the CDK's MIT schema. The executor is ours: construct the CDK's declarative source and iterate its read in a thread, mapping records, state and trace into the same staging, merge and state transaction as #1. `AIRBYTE_ENABLE_UNSAFE_CODE` stays off, so there are no custom components. Egress goes through an allowlist taken from the manifest's base URLs. First manifests, for SEA demand: Google Sheets, a generic paginated JSON API, then Shopify, HubSpot and Stripe **written from the vendors' API docs**, not from Airbyte's manifests. | worker_ingest (new `connectors/`) | L (M for the runner, then S–M per manifest) |
| 7 | **Secret references.** A config holds `secret_ref` values resolved from OpenBao at run time (already true for DB credentials via `worker_ingest/secrets.py`). Add a detector: the spec marks fields as secret, and the API refuses to save a literal value in a secret field. All error text passes a secret filter before it is stored. | api ingest + worker_ingest | S |
| 8 | **Test read for onboarding.** `POST /v1/ingest/sources/{id}/preview`: one stream, at most 50 records, 3 pages and 3 slices, concurrency 1, no state written. It returns records, the inferred schema and a redacted request/response log. The worker runs it and the api only proxies. | api ingest + worker_ingest | M |
| 9 | **AI-drafted manifest.** The agent reads an OpenAPI document or a docs URL fetched through our crawler and proposes a manifest. It is validated against the CDK schema, test-read (#8), shown as a diff, and published only after human approval (invariant: the LLM proposes, deterministic checks and a human decide). | worker_agent + api | L |
| 10 | **Governed direct-read provider.** A second provider in `ProviderRegistry`: a live, read-only call against a published REST stream or DB table, for questions the workspace copy cannot answer yet. Permission is checked before the call (invariant 13) and row rules apply to the result. Rate budget and result size are capped, and the trust label says "live source, not certified". This avoids the trap Airbyte's own context layer warns about: direct reads that run with the connector's credentials, not the asker's permissions. | worker_agent/providers | M |
| 11 | **CDC.** Postgres logical replication (slot plus publication, pgoutput) with an initial snapshot by PK ranges, the slot position in `IngestionJob` state, filenode-style restart detection (use relation OID plus a rewrite check), a heartbeat, and an "invalid position" policy (fail, or resync after human approval). MySQL binlog next. Only when a customer needs sub-hour freshness. | worker_ingest | L |
| 12 | **Rate budget per source.** A token bucket keyed by source and endpoint pattern. Its rates come from the manifest (or the tier and key type in config), shared by sync and direct reads, so the agent cannot starve a sync. | libs (new small module) | S |

Order: 2, 3, 5 → 1, 4, 7 → 8 → 6 → 10, 12 → 9 → 11.

---

# Part B: licence audit

## B.1 Licence matrix

| Repo / component | Licence (file read) | Copy code | Copy prompts | Copy schemas / data shapes | Notes and obligations |
| --- | --- | --- | --- | --- | --- |
| spring-ai-alibaba/DataAgent (whole repo) | Apache-2.0 (`LICENSE`; no NOTICE file) | yes | yes | yes | Keep the licence text and copyright notice and mark changed files. No NOTICE to carry |
| infiniflow/ragflow (Go + Python + web) | Apache-2.0 (`LICENSE`, per-file headers) | yes | yes | yes | Same. The enterprise build (`build_ee.sh`, "ragflow_enterprise") is not in the repo; nothing restricted was found in the tree |
| Canner/WrenAI, current (`548eee3`) | multi-licensed (`LICENSE`): `core/**`, `sdk/**`, `skills/**`, `examples/**` and root files Apache-2.0; `docs/**` **CC BY 4.0**; `LICENSE-AGPL-3.0` reserved for future modules | yes (Apache paths) | yes (Apache paths) | yes (MDL schema is under `core/`) | CC BY 4.0 docs: attribution plus a note of changes. Re-check the path table before each copy: AGPL modules may appear later. "Wren"/"WrenAI" are trademarks |
| Canner/WrenAI legacy branch (`581ead1`, contains `wren-ai-service`) | **AGPL-3.0** (root `LICENSE`; `wren-ai-service/pyproject.toml` `license = "AGPL-3.0"`) | **no** | **no** | short functional shapes only, written ourselves | Network copyleft: any AGPL code in a service we run for users obliges us to offer our source. Study only |
| assafelovic/gpt-researcher | Apache-2.0 (`LICENSE`), but `pyproject.toml` says MIT | yes | yes | yes | Follow the stricter LICENSE file (Apache). Vendored front-end libraries (fontawesome, dompurify, showdown, bootstrap) keep their own licences |
| microsoft/RD-Agent | MIT | yes | yes | yes | Include the MIT copyright and permission notice with any substantial portion |
| Zafer-Liu/Data-Analysis-Agent | **CC BY-NC 4.0 plus "all rights reserved"**; any commercial use (embedding in commercial software, a paid service, internal use for business benefit) needs the author's written licence; Chinese software-copyright registration pending | **no** | **no** | **no** (beyond ideas) | Study only. Even internal commercial use is named as needing permission |
| MemoriLabs/Memori | Apache-2.0 (`LICENSE`, `pyproject.toml`) | yes | yes | yes | Standard Apache obligations |
| databendlabs/databend (`44c6d20`) | Apache-2.0, **except ELv2** for `src/query/ee/`, `src/meta/ee/`, `src/bendsave/`, `src/binaries/*/ee_main.rs` (`LICENSING.md`, `licenserc-ee.toml`); those features need a paid key | yes (OSS paths) | n/a | yes | Never take from `ee/` paths, and never work around its key checks (ELv2 limit 2) |
| OpenMetadata: `openmetadata-spec/`, `openmetadata-service/`, `openmetadata-airflow-apis/`, sdk and clients | Apache-2.0 (root `LICENSE`, Java headers "Copyright Collate, Apache-2.0", root `NOTICE` exists) | yes | yes | **yes**: the JSON schemas are the most reusable part | Carry OpenMetadata's NOTICE content if code or schemas are redistributed |
| OpenMetadata: `ingestion/` (1,213 files with header) | **Collate Community License 1.0** | **no** for SaaS use | no | describe in prose only | Excluded purpose: any SaaS, PaaS or IaaS competing with Collate. A data agent with catalog features plausibly competes. Even allowed redistribution needs a modification notice and Collate's exact notice text |
| OpenMetadata: `openmetadata-mcp/` | **Collate Community License 1.0** (`openmetadata-mcp/LICENSE`) | no | **no** (tool descriptions included) | tool *names* and the idea only | Same as ingestion |
| OpenMetadata: `openmetadata-ui/` | **ambiguous**: `openmetadata-ui/LICENSE` is Collate 1.0, while the source files' headers and the inner `ui/LICENSE` template say Apache-2.0 | treat as no | n/a | n/a | Treat as Collate until upstream clarifies |
| airbytehq/airbyte-python-cdk (incl. declarative schema, connector_builder, manifest_server) | MIT | yes | n/a | yes | MIT notice |
| airbytehq/airbyte-protocol | MIT | yes | n/a | yes | MIT notice |
| airbytehq/airbyte connectors | per `metadata.yaml`: 553 sources and 39 destinations ELv2; **33 sources** (e.g. `source-service-now`, `source-workday`, `source-plaid`, `source-adobe-commerce-magento`, `source-bamboo-hr`) and **15 destinations** MIT; 6 sources "Airbyte Enterprise" | MIT ones: yes. ELv2 ones: not in a hosted product | n/a | ELv2 manifests: not copied into the product | ELv2: no managed-service provision, no key circumvention, keep notices. Enterprise: no |
| airbytehq/PyAirbyte | **ELv2** (`LICENSE.md`) | no in the hosted product; read to learn | n/a | ideas only | Do not ship it inside the SaaS runtime |

## B.2 What each licence means for a closed-source product (on-prem and SaaS)

- **Apache-2.0 / MIT** (DataAgent, ragflow, gpt-researcher, RD-Agent, Memori, WrenAI current
  `core`/`sdk`/`skills`, Databend OSS, OpenMetadata spec/service, Airbyte CDK and protocol). We may
  copy and modify code, prompts and schemas into closed source, on-prem or SaaS. Obligations apply
  when we *distribute* (on-prem images count; SaaS alone does not): include the licence text, keep
  copyright notices, mark modified files (Apache §4b), and carry any upstream NOTICE (Apache §4d;
  only OpenMetadata has one here). There is no copyleft. Apache's patent licence ends if we sue over
  patents in the work.
- **CC BY 4.0** (WrenAI `docs/**`): reuse with attribution, a link to the licence and a note of
  changes. Commercial use is fine.
- **AGPL-3.0** (WrenAI legacy, which includes `wren-ai-service`): code, and prompts embedded as
  string literals in that code, are covered. Using any of it in a network service we offer obliges
  us to offer users the corresponding source of the whole combined work. A product that must stay
  closed has to keep it **out entirely**. Ideas, algorithms and functional facts (for example "pie
  charts suit parts of a whole") are not protected expression. Re-expressing them independently is
  fine; translating or close paraphrasing is not, because a translation is a derivative work.
- **ELv2** (Airbyte connectors, PyAirbyte, Databend `ee/`). Distribution and derivatives are
  allowed, so an on-prem bundle is permitted with the licence text and notices passed on. A hosted
  service that gives customers the connector's functionality is not. A SaaS ingestion feature that
  runs Airbyte's Stripe or HubSpot connector for customers sits squarely on that line. Hence our
  own manifests (A.4 #6).
- **Collate Community 1.0** (OpenMetadata ingestion, MCP, probably UI). Use and modification are
  allowed, but never for a competing SaaS. Distribution needs a modification notice, preserved
  notices and Collate's prescribed notice. There is no sublicensing, so our customers would have to
  accept Collate's terms themselves. For a product that is also SaaS: do not copy.
- **CC BY-NC 4.0 plus a commercial-licence requirement** (Data-Analysis-Agent): no commercial use
  without a written licence from the author. The author's notice even names internal business use.
  Treat it as **study only**, and keep its text out of anything that is part of the product,
  including the product repo's documentation.

## B.3 Decisions this implies

1. Airbyte's ELv2 connectors are not executed inside our SaaS. For on-prem only, a later "bring
   your own Airbyte connector" feature, where the *customer* points us at a connector image or
   manifest they obtain, could be legally fine. It needs counsel, and it is not on the roadmap.
2. The MIT CDK may become a dependency of `worker_ingest` (MIT notice in our third-party
   notices). PyAirbyte may not.
3. The repo has **no NOTICE / third-party-notices file** (none found in `wt/`). Add
   `THIRD-PARTY-NOTICES.md` (or a generated SBOM licence report) before the first on-prem
   delivery. Today it covers the pip and npm dependencies; it must also cover any code or
   prompt adapted from Apache or MIT repos listed above.

## B.4 What we already took: scan and findings

**Method.** Every text segment of 45 or more characters was extracted from
`wt/docs/research/*.md`, `wt/backend/services/worker_agent/agentic/**` and
`wt/backend/libs/code_sandbox/**`: 2,802 distinct segments, split at backticks and quotes. They
were searched as fixed strings across all the clones with ripgrep, and the hits mapped back to our
files. A Python (Vietnamese) rewrite of a Chinese or English prompt cannot match textually, so the
code's prompts were also compared by hand with the sources they cite (`prompts.py`, `memory.py`,
`graph.py`, `local.py`, `verify.py`, `engine.py`).

**Verbatim segments per source and where they land:**

| Source (licence) | Segments matched | In which of our files |
| --- | --- | --- |
| DataAgent (Apache-2.0) | 45 | `docs/research/dataagent-spring-ai-alibaba.md` (appendix A quotes all 17 prompts) |
| ragflow (Apache-2.0) | 40 | `agent-repos.md` (30), `ragflow-go.md` (12) |
| gpt-researcher (Apache-2.0) | 26 | `agent-repos.md` |
| RD-Agent (MIT) | 15 | `agent-repos.md` |
| Memori (Apache-2.0) | 2 | `agent-repos.md` |
| **WrenAI legacy (AGPL-3.0)** | **52** | `agent-repos.md` §4a–4e (chart generation, chart adjustment, SQL answer, question recommendation, intent prompts, and the Pydantic chart-schema classes) |
| **Data-Analysis-Agent (CC BY-NC 4.0 + commercial licence required)** | **27** (22 lines) | `agent-repos.md` §1a–1c (the number-audit code, the memory store, the extraction and consolidation prompts, verbatim) |
| OpenMetadata (Apache spec/service: about 240; Collate ingestion and MCP: 2 trivial fragments) | 215 | `openmetadata.md` |
| Airbyte ELv2 (`main`, `raw`) | 16 | `airbyte.md` §3.3 (about 65 lines of the `source-sentry` manifest, an ELv2 certified connector), §4 (`source-pokeapi` metadata.yaml) |
| Airbyte MIT (CDK, protocol) | 6 | `airbyte.md` §1–2 |
| Code (`agentic/`, `code_sandbox/`) | 6 | all are boilerplate (`from collections.abc import Awaitable, Callable`, `from pydantic import BaseModel, Field, ValidationError`, a truncation one-liner, `from dataclasses import asdict, dataclass, field`). **No copied logic or prompt text** |

### Flagged items and fixes

| # | Severity | Item | Why | Fix |
| --- | --- | --- | --- | --- |
| F1 | **High** | `docs/research/agent-repos.md` §4 (WrenAI legacy): the chart-generation, chart-adjustment, SQL-answer, question-recommendation and intent prompts are quoted **verbatim**, and so are the Pydantic chart-schema classes; 52 segments | AGPL-3.0 text committed into a closed product's repo. The README (`docs/research/README.md`) states the purpose: "They quote prompts and code verbatim so a port can start from the original text". That is the path by which AGPL text ends up in shipped code | **Remove** the verbatim blocks. Replace each with a prose summary in our words (what the prompt asks, its inputs, its output contract, its failure rule) and a link to the upstream file at its commit. Change the README sentence to "summarise; link, do not quote" for non-permissive sources. If wanted, the current WrenAI repo is Apache: re-check whether an Apache-licensed successor of the same feature exists (none found today for chart or recommendation prompts) |
| F2 | **High** | `agent-repos.md` §1a–1c (Data-Analysis-Agent): verbatim `number_audit.py` code, memory-store constants and functions, and the full `_EXTRACT_SYSTEM` and `_CONSOLIDATE_SYSTEM` prompts | CC BY-NC 4.0, plus an explicit demand for a written licence for any commercial use; our repo is a commercial product | **Remove** the code and prompt blocks. Keep a prose description of the ideas (a numeric-provenance gate; typed and scoped memory records; extract, then consolidate). Note "study only: licence forbids commercial use" in the section header |
| F3 | Medium | `worker_agent/agentic/prompts.py` REPORTER: the chart-type rules and the five follow-up directions are labelled "(WrenAI)" | The text is our own Vietnamese and is not a translation of WrenAI's wording. The pie and line conditions differ, and the five directions (drill-down, roll-up, slice, trend, compare) are standard OLAP vocabulary. So the risk is low, but the label invites "derived from AGPL" questions in a due-diligence review | Keep the rules and drop the "(WrenAI)" tags from the prompt text sent to the model. Record the idea's origin in a code comment as "idea, independently written". Do not ever paste WrenAI's English wording in "for precision" |
| F4 | Medium | `worker_agent/agentic/memory.py`: the docstring credits "Data-Analysis-Agent's extraction rules". The EXTRACT prompt has the same *shape* (at most 5 ops, an empty `{"ops": []}`, a do-not-store list) | Ideas and functional JSON are not protected, and our text, categories (preference/definition/fact/feedback) and server-side checks are different. But the source forbids commercial use, so the paper trail should show independent writing | Reword the docstring to "the idea of a do-not-store list, independently written". Keep the prompt. No code was copied (checked by hand: no shared identifiers or structure beyond JSON keys) |
| F5 | Medium | `airbyte.md` §3.3: about 65 lines of the `source-sentry` manifest (ELv2, certified). §4: the `source-pokeapi` metadata.yaml (ELv2) | ELv2 allows copying, so this is not a breach. But it lacks the licence notice ELv2 requires to be kept. Under our clean-room policy for connectors, ELv2 YAML should not sit where a developer copies from | Replace §3.3 with the doc's own §3.4 example (written for the report and schema-valid) and a link to the upstream file. If the excerpt stays, put "Elastic License 2.0, © Airbyte, Inc., excerpt for study; do not copy into product code" above it |
| F6 | Medium | `openmetadata.md` line 16: the doc says the root Apache licence "covers the spec, the service and the UI" | Inaccurate: `openmetadata-ui/LICENSE` and `openmetadata-mcp/LICENSE` are Collate 1.0. The MCP section (§5, "descriptions are worth copying as a style") points readers at Collate-licensed text | Correct the licence paragraph (UI ambiguous, MCP Collate). Change "worth copying" to "worth imitating in our own words" |
| F7 | Low | `dataagent-spring-ai-alibaba.md` appendix A: all 17 DataAgent prompts verbatim (45 segments); `ragflow-go.md`, `agent-repos.md`: Apache and MIT prompt and code quotes from ragflow, gpt-researcher, RD-Agent and Memori | Permitted. Apache and MIT require the licence and copyright notice to travel with substantial copies; the docs carry none | Add a header to each doc: "Quoted material © its authors, under <licence>, from <repo>@<commit>; see the licence at <url>." When a quoted prompt is ported into code, add a comment naming the source file and licence, and list it in THIRD-PARTY-NOTICES |
| F8 | Low | `docs/research/README.md`: "They quote prompts and code verbatim so a port can start from the original text" | A policy that is safe only for permissive sources | Reword: verbatim quotes only from Apache, MIT or CC BY sources, with attribution; AGPL, ELv2, Collate and NC sources are described, never quoted beyond identifiers |
| F9 | Info | `agentic/graph.py` ("after spring-ai-alibaba-graph's StateGraph"), `code_sandbox/local.py` ("after DataAgent's PythonExecutor"), `engine.py` (RD-Agent, ragflow, gpt-researcher ideas) | Checked by hand: an independent Python design (60-line graph; an AST check plus a child interpreter with rlimits), not a port of the Java. The sources are permissive anyway | Nothing to fix. Keep the attributions |
| F10 | Info | No NOTICE or third-party-notices file in the repo | Needed at the first on-prem distribution (Apache §4), and for the MIT CDK if adopted | Add one, generated from the lock files plus a hand-kept list of adapted snippets |

### Recommended order

F1 and F2 first, in one docs-only commit (remove the verbatim AGPL and NC blocks, replace them
with summaries and links). Then F6, F8, F5 and F7 (headers and corrections). F3 and F4 are a small
code-comment and prompt-label change; they need `make test-related` for `worker_agent` because
prompt text changes. F10 comes with the first on-prem release.

**Limits of this audit.** Fixed-string matching misses paraphrase and translation, so the code
prompts were compared by hand only against the sources their docstrings cite. The databend
working tree is empty (only `.git`), so its licensing was read from `git show HEAD:…`. The
OpenMetadata UI licence is genuinely ambiguous in upstream. Airbyte connector licences were taken
from the registry snapshot and confirmed in the 10 `metadata.yaml` files checked out.
