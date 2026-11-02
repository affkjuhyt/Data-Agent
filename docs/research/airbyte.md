# Airbyte, read deeply: protocol, CDK, low-code YAML, registry, destinations, PyAirbyte, and what Data Agent should take

> Licences: the Airbyte CDK and protocol are MIT (quoted with © Airbyte, Inc.); connectors and PyAirbyte are ELv2 and are described, not quoted. https://github.com/airbytehq/airbyte, airbyte-python-cdk, airbyte-protocol at the commits named below.

Research date 2026-11-03. All paths below are relative to the clones under
`research3/airbyte/` (scratchpad). Repo contents were treated as data.

## 0. What was read, at which commit

| Repo | Commit | Clone | Licence |
| --- | --- | --- | --- |
| `airbytehq/airbyte` (monorepo: connectors, docs) | `d9cd7e7` (2026-11-02) | **not a full clone.** A full `git clone` ran ~10 min and reached 34 MB of a multi-GB history (~50 KB/s here), and a `--filter=blob:none` clone was no faster, so both were stopped. Used instead: `--depth 1 --filter=blob:none --sparse`, which holds the full file tree (700 connector dirs listed) with the docs and the connectors I read checked out. A few connector files were fetched raw (`raw/`) | connectors per `metadata.yaml`: ELv2 mostly |
| `airbytehq/airbyte-python-cdk` | `fd9e6c22` (2026-10-30), tag `v7.45.0` | full | MIT |
| `airbytehq/airbyte-protocol` | `79375c2` (2026-01-26), protocol `0.19.0`, schema `version: 0.3.2` | full | MIT |
| `airbytehq/PyAirbyte` | `1182d56` (2026-10-30) | depth 1 | **ELv2** |

The CDK and the protocol moved out of the monorepo. The monorepo's `airbyte-cdk/` still holds `bulk`
(the Kotlin CDK the Java/Kotlin DB connectors use), `java` and `python` stubs. The Python CDK lives
only in `airbyte-python-cdk`, and the protocol models only in `airbyte-protocol`.

Inventory at HEAD (`git ls-tree` on the monorepo, plus the live registry
`https://connectors.airbyte.com/files/registries/v0/oss_registry.json`, saved as `raw/oss_registry.json`):

- 616 `source-*` and 82 `destination-*` directories. **520 sources are manifest-only** (a root
  `manifest.yaml` and no Python package), 56 of them with a `components.py`. 20 Python sources embed
  a manifest, and 22 sources are Java/Kotlin (Postgres, MySQL, MSSQL among them).
- OSS registry: 592 sources and 54 destinations. Language: `manifest-only` 509, `python` 59, `java` 24.
  `supportLevel`: `community` 520, `certified` 72. `license`: ELv2 549 (+4 spelled `Elv2`), MIT 33,
  "Airbyte Enterprise" 6.

The licence split matters for the recommendations later. The engine that runs YAML connectors (the CDK)
is MIT. Most of the connector YAMLs themselves are ELv2, and PyAirbyte is ELv2.

---

## 1. The Airbyte Protocol

Source of truth: `protocol/protocol-models/src/main/resources/airbyte_protocol/v0/airbyte_protocol.yaml`
(1034 lines, JSON-Schema draft-07). Narrative: `main/docs/platform/understanding-airbyte/airbyte-protocol.md`.

### 1.1 The actor interface

```
spec()                                   -> ConnectorSpecification
check(Config)                            -> AirbyteConnectionStatus
discover(Config)                         -> AirbyteCatalog                       (sources)
read(Config, ConfiguredAirbyteCatalog, State) -> Stream<AirbyteRecordMessage | AirbyteStateMessage>
write(Config, ConfiguredAirbyteCatalog, Stream<AirbyteMessage>(stdin)) -> Stream<AirbyteStateMessage>   (destinations)
```

On the CLI these are `spec`, `check --config c.json`, `discover --config c.json`,
`read --config c.json --catalog cat.json [--state s.json]` and `write --config … --catalog …` (stdin).
The doc states these invariants:

- Every output is one JSON object per line, wrapped in the `AirbyteMessage` envelope. Anything not
  wrapped is dropped. STDERR carries only logs.
- Any method may also emit any number of `LOG` and `TRACE` messages.
- A source has no ingress. A destination reads records on stdin and **echoes a STATE only once every
  record before it is committed**, in the order received (per stream, or globally for GLOBAL state).
- Since 2025 there is a second data channel: **Socket mode** (`DATA_CHANNEL_MEDIUM=SOCKET`,
  `DATA_CHANNEL_FORMAT=PROTOBUF`). Records go source to destination over several Unix sockets, and
  state messages carry `partition_id` and an incrementing `id` with `sourceStats.recordCount`, so the
  destination commits a checkpoint only when the counts match. Connectors opt in via
  `connectorIPCOptions` in `metadata.yaml` (e.g. source-postgres: `supportedTransport: [SOCKET, STDIO]`).

### 1.2 Message shapes (verbatim, descriptions trimmed)

```yaml
AirbyteMessage:
  type: object
  additionalProperties: true
  required: [type]
  properties:
    type:
      type: string
      enum: [RECORD, STATE, LOG, SPEC, CONNECTION_STATUS, CATALOG, TRACE, CONTROL, DESTINATION_CATALOG]
    log:              {"$ref": "#/definitions/AirbyteLogMessage"}
    spec:             {"$ref": "#/definitions/ConnectorSpecification"}
    connectionStatus: {"$ref": "#/definitions/AirbyteConnectionStatus"}
    catalog:          {"$ref": "#/definitions/AirbyteCatalog"}
    record:           {"$ref": "#/definitions/AirbyteRecordMessage"}
    state:            {"$ref": "#/definitions/AirbyteStateMessage"}
    trace:            {"$ref": "#/definitions/AirbyteTraceMessage"}
    control:          {"$ref": "#/definitions/AirbyteControlMessage"}
    destination_catalog: {"$ref": "#/definitions/DestinationCatalog"}

AirbyteRecordMessage:
  required: [stream, data, emitted_at]
  properties:
    namespace:  {type: string}
    stream:     {type: string}
    data:       {type: object}
    emitted_at: {type: integer, description: "epoch in millisecond"}
    meta:       {"$ref": "#/definitions/AirbyteRecordMessageMeta"}       # {changes: [...]}
    file_reference: {"$ref": "#/definitions/AirbyteRecordMessageFileReference"}

AirbyteRecordMessageMetaChange:
  required: [field, reason, change]
  properties:
    field:  {type: string}
    change: {type: string, enum: [NULLED, TRUNCATED]}
    reason:
      type: string
      enum: [SOURCE_RECORD_SIZE_LIMITATION, DESTINATION_RECORD_SIZE_LIMITATION, PLATFORM_RECORD_SIZE_LIMITATION,
             SOURCE_FIELD_SIZE_LIMITATION, DESTINATION_FIELD_SIZE_LIMITATION, PLATFORM_FIELD_SIZE_LIMITATION,
             SOURCE_SERIALIZATION_ERROR, DESTINATION_SERIALIZATION_ERROR, PLATFORM_SERIALIZATION_ERROR,
             SOURCE_RETRIEVAL_ERROR, DESTINATION_TYPECAST_ERROR]

AirbyteStateMessage:
  properties:
    type:   {"$ref": "#/definitions/AirbyteStateType"}     # GLOBAL | STREAM | LEGACY
    stream: {"$ref": "#/definitions/AirbyteStreamState"}
    global: {"$ref": "#/definitions/AirbyteGlobalState"}
    data:   {type: object, description: "(Deprecated) the state data"}
    sourceStats:      {"$ref": "#/definitions/AirbyteStateStats"}
    destinationStats: {"$ref": "#/definitions/AirbyteStateStats"}
AirbyteStreamState:
  required: [stream_descriptor]
  properties:
    stream_descriptor: {"$ref": "#/definitions/StreamDescriptor"}   # {name, namespace?}
    stream_state:      {"$ref": "#/definitions/AirbyteStateBlob"}   # opaque object
AirbyteGlobalState:
  required: [stream_states]
  properties:
    shared_state:  {"$ref": "#/definitions/AirbyteStateBlob"}
    stream_states: {type: array, items: {"$ref": "#/definitions/AirbyteStreamState"}}
AirbyteStateStats:
  properties:
    recordCount:         {type: number}   # a double "to account for integer overflows"
    rejectedRecordCount: {type: number}
    additionalStats:     {type: object, additionalProperties: {type: number}}

AirbyteLogMessage:
  required: [level, message]
  properties:
    level: {type: string, enum: [FATAL, ERROR, WARN, INFO, DEBUG, TRACE]}
    message: {type: string}
    stack_trace: {type: string}

AirbyteTraceMessage:
  required: [type, emitted_at]
  properties:
    type: {type: string, enum: [ERROR, ESTIMATE, STREAM_STATUS, ANALYTICS]}
    emitted_at: {type: number}
    error:
      required: [message]
      properties:
        message: {type: string}            # user-facing
        internal_message: {type: string}
        stack_trace: {type: string}
        failure_type: {type: string, enum: [system_error, config_error, transient_error]}
        stream_descriptor: {"$ref": "#/definitions/StreamDescriptor"}
    estimate:      {required: [name, type], properties: {name, type: [STREAM, SYNC], namespace, row_estimate, byte_estimate}}
    stream_status:
      required: [stream_descriptor, status]
      properties:
        status: {enum: [STARTED, RUNNING, COMPLETE, INCOMPLETE]}
        reasons: [{type: RATE_LIMITED, rate_limited: {quota_reset: <ms>}}]
    analytics: {required: [type], properties: {type: string, value: string}}

AirbyteControlMessage:            # connector asks the platform to persist an updated config (e.g. rotated refresh token)
  required: [type, emitted_at]
  properties:
    type: {enum: [CONNECTOR_CONFIG]}
    connectorConfig: {required: [config], properties: {config: object}}

AirbyteConnectionStatus:
  required: [status]
  properties:
    status: {type: string, enum: [SUCCEEDED, FAILED]}
    message: {type: string}
```

### 1.3 Catalog shapes (verbatim, trimmed)

```yaml
AirbyteCatalog:
  required: [streams]
  properties: {streams: {type: array, items: {"$ref": "#/definitions/AirbyteStream"}}}
AirbyteStream:
  required: [name, json_schema, supported_sync_modes]
  properties:
    name: {type: string}
    json_schema: {type: object}                       # JSON Schema of a record
    supported_sync_modes: {type: array, minItems: 1, items: {"$ref": "#/definitions/SyncMode"}}
    source_defined_cursor: {type: boolean}
    default_cursor_field: {type: array, items: {type: string}}               # a path
    source_defined_primary_key: {type: array, items: {type: array, items: {type: string}}}  # list of paths
    namespace: {type: string}
    is_resumable: {type: boolean}
    is_file_based: {type: boolean}
ConfiguredAirbyteCatalog:
  required: [streams]
  properties: {streams: {type: array, items: {"$ref": "#/definitions/ConfiguredAirbyteStream"}}}
ConfiguredAirbyteStream:
  required: [stream, sync_mode, destination_sync_mode]
  properties:
    stream: {"$ref": "#/definitions/AirbyteStream"}
    sync_mode: {"$ref": "#/definitions/SyncMode", default: full_refresh}
    cursor_field: {type: array, items: {type: string}}     # REQUIRED if incremental
    destination_sync_mode: {"$ref": "#/definitions/DestinationSyncMode", default: append}
    destination_object_name: {type: string}
    primary_key: {type: array, items: {type: array, items: {type: string}}}  # REQUIRED for *_dedup
    generation_id: {type: integer}          # refresh generations: destination may delete rows < minimum_generation_id
    minimum_generation_id: {type: integer}
    sync_id: {type: integer}
    include_files: {type: boolean}
SyncMode: {type: string, enum: [full_refresh, incremental]}
DestinationSyncMode: {type: string, enum: [append, overwrite, append_dedup, update, soft_delete]}
```

`ConnectorSpecification` has `connectionSpecification` (the JSON Schema of the config; secret fields
carry `airbyte_secret: true`), `documentationUrl`, `supported_destination_sync_modes`, `advanced_auth`
(OAuth flow wiring) and `protocol_version`. CDC is not a sync mode in the protocol. It is
`incremental` with a source-defined cursor (LSN/binlog position) and `_ab_cdc_*` columns on the records.

### 1.4 What a `read` looks like on the wire

```json
{"type":"TRACE","trace":{"type":"STREAM_STATUS","emitted_at":1730600000000,"stream_status":{"stream_descriptor":{"name":"orders"},"status":"STARTED"}}}
{"type":"RECORD","record":{"stream":"orders","data":{"id":"o-1","updated_at":"2026-10-01T08:00:00Z","total":120.5},"emitted_at":1730600000123}}
{"type":"STATE","state":{"type":"STREAM","stream":{"stream_descriptor":{"name":"orders"},"stream_state":{"state_type":"date-range","slices":[{"start":"2026-09-01T00:00:00Z","end":"2026-10-01T00:00:00Z","most_recent_cursor_value":"2026-10-01T08:00:00Z"}]}},"sourceStats":{"recordCount":1.0}}}
{"type":"TRACE","trace":{"type":"STREAM_STATUS","emitted_at":1730600000999,"stream_status":{"stream_descriptor":{"name":"orders"},"status":"COMPLETE"}}}
```

### 1.5 State semantics (the part worth copying)

From the protocol doc, §"State & Checkpointing":

- **State is a black box owned by the source.** Platform and destination never read inside
  `stream_state`. Null state means start from the beginning.
- **Commit rule.** A state is handed to the next run only once *both* the source emitted it *and* the
  destination echoed it, which means every record before it is committed. This gives at-least-once
  delivery ("Airbyte is a deliver at-least-once system", `resumability.md`). Deduplication is the
  destination's job, done on the primary key.
- **STREAM vs GLOBAL.** STREAM state isolates streams: per-stream reset, and streams can run in
  parallel. GLOBAL is for sources whose position is shared, as in CDC, where one WAL position covers
  all tables. LEGACY is deprecated.
- **Principles the CDK enforces.** Always emit state, even on full refresh and even for an empty stream.
  Never emit the same state twice in a row with records between. Emit state whenever a resume would
  be meaningful. Destinations return states in order.
- **Cursor resolution.** If `source_defined_cursor`, the source's cursor wins. Otherwise the configured
  `cursor_field`, then `default_cursor_field`. If none is set the configuration is illegal.
- **Schema mismatch never fails a sync.** A stream or field that is missing on either side is skipped.
- **Resumable full refresh.** Full refresh is also checkpointed, by page token or by CTID ranges
  (`select * from users where CTID >= X AND CTID < Y`), so a retry resumes and does not restart.

---

## 2. Python CDK: control flow, with paths (`cdk/airbyte_cdk/…`)

### 2.1 Entrypoint (`entrypoint.py`)

`launch(source, argv)` → `AirbyteEntrypoint.run(parsed_args)`, which:

1. calls `source.spec(logger)` first, always;
2. sets a temp dir for the request cache (`ENV_REQUEST_CACHE_PATH`; streams can cache HTTP responses
   in SQLite, which parent streams use);
3. for `check`/`discover`/`read`: `read_config` → `configure` → `set_up_secret_filter`, which collects
   every value under an `airbyte_secret` field of the spec and masks it in all logs. It validates the
   config against `connectionSpecification` (`check_config_against_spec_or_exit`);
4. `read`: `read_catalog`, `read_state`, then `source.read(...)`. Every message passes through
   `handle_record_counts`, which counts RECORDs per stream and **stamps `sourceStats.recordCount` on
   each STATE**, then resets the counter. A memory monitor checks usage after each message. On a
   fail-fast it flushes the queued state messages before raising, "so the platform receives the last
   committed state";
5. `check`: a `config_error` trace becomes a `FAILED` connection status with exit 0. Any other failure
   type re-raises and exits non-zero. Recent versions add *section checks*: `check-section` validates
   one step of a stepped setup form.
6. **SSRF guard** `_init_internal_request_filter()`. When `DEPLOYMENT_MODE=cloud` it monkeypatches
   `requests.Session.send` and refuses non-https schemes and any hostname that resolves to a private
   IP (`ipaddress.ip_address(ip).is_private`).

### 2.2 `AbstractSource` (`sources/abstract_source.py`)

You implement `check_connection(logger, config) -> (bool, error)` and `streams(config) -> list[Stream]`.
`discover` is `[s.as_airbyte_stream() for s in streams]`. `read`:

```
for configured_stream in catalog.streams:
    emit STREAM_STATUS STARTED
    _read_stream():  stream_state = state_manager.get_stream_state(name, ns)
                     stream_instance.state = stream_state
                     for r in stream_instance.read(configured_stream, …, state_manager, internal_config):
                         first record -> emit STREAM_STATUS RUNNING
                         yield queued messages (logs, states from the message repository), then the record
    emit STREAM_STATUS COMPLETE
  except: emit INCOMPLETE + an ERROR trace (AirbyteTracedException, failure_type) and continue with
          the next stream unless stop_sync_on_stream_failure
at the end: if any stream failed, raise a combined config_error so the exit code is non-zero
```

### 2.3 `Stream.read` and checkpointing (`sources/streams/core.py`)

The `CheckpointReader` (`streams/checkpoint/`) walks slices. For each slice it calls
`read_records(sync_mode, stream_slice, stream_state, cursor_field)`, observes each record into the
cursor, emits a STATE every `state_checkpoint_interval` records, emits a STATE at the end of every
slice, and emits a final STATE. There are three modes (`_checkpoint_mode`): FULL_REFRESH, INCREMENTAL,
and RESUMABLE_FULL_REFRESH (the page token is the cursor). Streams declare `primary_key`,
`cursor_field`, `source_defined_cursor`, `supports_incremental` and `is_resumable`. The
`IncrementalMixin` exposes a `state` property with a getter and a setter.

### 2.4 HTTP: `HttpStream` + `HttpClient` (`sources/streams/http/`)

- `HttpStream` hooks: `url_base`, `path()`, `request_params()`, `request_headers()`,
  `request_body_json()`, `next_page_token(response)`, `parse_response(response)`,
  `get_backoff_strategy()`, `get_error_handler()`. `_read_pages` loops until `next_page_token` is `None`.
- `HttpClient.send_request` → `_send_with_retry` (the `backoff` library, `max_retries`, `max_time`) →
  `_send` → `_handle_error_resolution`. Error handling is a table from status code or exception to
  `ErrorResolution(response_action, failure_type, error_message)` (`error_handlers/default_error_mapping.py`):
  400/404/405 FAIL `system_error`; 401/403 FAIL `config_error`; 408/500/502/503 RETRY `transient_error`;
  429 `RATE_LIMITED`; `RequestException` RETRY. `ResponseAction` also has `IGNORE`,
  `RESET_PAGINATION`, `REFRESH_TOKEN_THEN_RETRY`, `REDUCE_PAGE_SIZE` and `SPLIT_REQUEST_WINDOW`; the
  last two are new and let a retriever shrink its page size or halve a datetime window on a timeout.
- Auth (`requests_native_auth/`): `TokenAuthenticator`, `BasicHttpAuthenticator`,
  `Oauth2Authenticator` and `SingleUseRefreshTokenOauth2Authenticator`. The last emits a CONTROL
  message with the rotated refresh token so the platform persists it. Multiple-token rotation is
  supported too.
- Rate limits (`sources/streams/call_rate.py`): `APIBudget` with `FixedWindowCallRatePolicy`,
  `MovingWindowCallRatePolicy` and `UnlimitedCallRatePolicy`, `Rate(limit, interval)` and
  `HttpRequestMatcher` (method/url/params/headers). The budget is acquired before each request and
  updated from `ratelimit_remaining`/`reset` headers.

### 2.5 Concurrent CDK (what declarative sources actually run on)

`ConcurrentSource` (`sources/concurrent_source/`) runs a thread pool. Partition generators put
partitions on a bounded `Queue(maxsize=10_000)`, workers read partitions, and the main thread
(`ConcurrentReadProcessor`) emits records, statuses and states. Cursors live in
`sources/streams/concurrent/cursor.py`:

- `ConcurrentCursor.observe(record)` tracks the max cursor value per partition. It is not locked,
  because partitions are disjoint keys.
- `close_partition(partition)`, under a lock, appends `{start, end, most_recent_cursor_value}` to
  `state["slices"]`, **merges contiguous intervals** (`merge_intervals`) and emits a STATE. The state is
  therefore *a set of completed intervals*, not a single high-water mark. Out-of-order partition
  completion can never move the watermark past a hole. `_get_latest_complete_time` takes the first
  merged interval's end as the sequential watermark.
- State converters (`state_converters/`): `date-range` (ISO or epoch) and `integer`. The concurrent
  state `{"state_type": "date-range", "slices": [...], "legacy": {...}}` converts back to the old
  `{cursor_field: value}` form.
- Per-partition state (`declarative/incremental/concurrent_partition_cursor.py`), for substreams
  ("lines of each order"):

```json
{ "states": [ {"partition": {"order_id": "o-1"}, "cursor": {"updated_at": "2026-05-27T00:00:00Z"}} ],
  "state": {"updated_at": "2026-05-27T00:00:00Z"},
  "use_global_cursor": false,
  "lookback_window": 10,
  "parent_state": {"orders": {"updated_at": "2026-05-27T00:00:00Z"}} }
```

  Above `SWITCH_TO_GLOBAL_LIMIT = 10_000` partitions it falls back to one global cursor, with a
  lookback equal to the previous sync's duration. Partitions are capped at 25,000 in memory.

---

## 3. The low-code declarative framework (YAML-only connectors)

### 3.1 How a manifest becomes a running source

`ConcurrentDeclarativeSource.__init__` (`sources/declarative/concurrent_declarative_source.py`):

1. `_pre_process_manifest`: `ManifestReferenceResolver` resolves `$ref: "#/definitions/…"` (an object
   merged with sibling overrides) and `"#/…"` strings. `ManifestComponentTransformer` propagates
   `$parameters` downward and infers missing `type:`s from the parent field.
2. `_validate_source`: JSON-Schema validation against `declarative_component_schema.yaml`
   (6069 lines, ~150 component definitions).
3. Optional `manifest_migrations/` and normalization (dedupes repeated blocks into `definitions`).
4. `ModelToComponentFactory` (`parsers/model_to_component_factory.py`, 6094 lines) turns the Pydantic
   models (`models/declarative_component_schema.py`, generated) into runtime objects.
5. `concurrency_level` sizes the thread pool. `api_budget` installs the rate limiter. Config
   migrations and transformations (`spec.config_normalization_rules`) rewrite old configs.
6. Custom Python (`type: CustomRetriever`, `class_name: components.X`, or an injected
   `__injected_components_py`) runs only if `AIRBYTE_ENABLE_UNSAFE_CODE=true`
   (`parsers/custom_code_compiler.py`), and is checksum-verified.

The top-level schema (verbatim):

```yaml
title: DeclarativeSource
required: [type, check, version]
anyOf: [{required: [streams]}, {required: [dynamic_streams]}]
properties:
  type: {enum: [DeclarativeSource]}
  check: {anyOf: [CheckStream, CheckDynamicStream]}
  streams: {type: array, items: {anyOf: [ConditionalStreams, DeclarativeStream, StateDelegatingStream]}}
  dynamic_streams: {type: array, items: DynamicDeclarativeStream}
  version: {type: string}           # CDK version the manifest was built against
  schemas: Schemas
  definitions: {type: object}       # free-form; target of $ref
  spec: Spec
  concurrency_level: ConcurrencyLevel
  api_budget: HTTPAPIBudget
  stream_groups: {additionalProperties: StreamGroup}   # streams that must not read simultaneously
  max_concurrent_async_job_count: {type: [integer, string]}
  metadata: {type: object}          # builder-only: testedStreams, autoImportSchema, assist
  description: {type: string}
additionalProperties: false
```

### 3.2 Component vocabulary (grouped)

- **Stream**: `DeclarativeStream {name, retriever, incremental_sync, primary_key, schema_loader,
  transformations, state_migrations, file_uploader}`. `StateDelegatingStream` picks a full-refresh
  or an incremental retriever depending on whether state exists. `DynamicDeclarativeStream` builds
  streams from a template plus a resolver (`HttpComponentsResolver` lists objects from an endpoint,
  `ConfigComponentsResolver` reads them from config), which is how "one stream per report or table"
  sources work.
- **Retrievers**: `SimpleRetriever {requester, record_selector, paginator, partition_router, decoder,
  pagination_reset, page_size_reduction, request_window_splitting}`. `AsyncRetriever` covers report APIs:
  create a job, poll it, download the result, with `status_mapping`, `creation_requester`,
  `polling_requester`, `download_requester` and `abort_requester`.
- **Requester**: `HttpRequester {url_base, path, http_method, authenticator, request_parameters,
  request_headers, request_body (JSON/form/GraphQL/plain), error_handler, use_cache, timeouts,
  query_properties}`.
- **Auth**: `ApiKeyAuthenticator`, `BearerAuthenticator`, `BasicHttpAuthenticator`,
  `OAuthAuthenticator` (refresh, client-credentials, profile assertion, refresh-token updater),
  `JwtAuthenticator`, `SessionTokenAuthenticator`, `SelectiveAuthenticator` (chosen from a config
  value), `RateLimitedMultipleTokenAuthenticator`, `NoAuth`, `CustomAuthenticator`.
- **Pagination**: `DefaultPaginator {pagination_strategy, page_size_option, page_token_option}`, with
  `CursorPagination {cursor_value, stop_condition, page_size}`, `OffsetIncrement` and `PageIncrement`.
  Also `NoPagination`.
- **Selection**: `RecordSelector {extractor: DpathExtractor | CombinedExtractor | ResponseToFileExtractor
  | Custom, record_filter: RecordFilter{condition}, schema_normalization}`. Decoders: Json, Jsonl,
  JsonItems, Xml, Csv, Gzip, Zipfile, Iterable.
- **Incremental**: `DatetimeBasedCursor {cursor_field, datetime_format, cursor_datetime_formats,
  start_datetime, end_datetime, step, cursor_granularity, lookback_window, start_time_option,
  end_time_option, partition_field_start/end, is_data_feed, is_client_side_incremental,
  is_compare_strictly, global_substream_cursor, clamping}`. Also `IncrementingCountCursor` and
  `SyncTokenCursor`. `is_data_feed: true` means the API returns newest first with no date filter:
  read pages until a record older than the state appears. `is_client_side_incremental` filters
  records locally.
- **Partitioning**: `ListPartitionRouter`, `SubstreamPartitionRouter {parent_stream_configs:
  [ParentStreamConfig {stream, parent_key, partition_field, request_option, incremental_dependency,
  extra_fields}]}`, `GroupingPartitionRouter` and `UnionPartitionRouter`.
- **Errors and backoff**: `DefaultErrorHandler {backoff_strategies, max_retries, response_filters:
  [HttpResponseFilter {action: SUCCESS|FAIL|RETRY|IGNORE|RATE_LIMITED|…, http_codes,
  error_message_contains, predicate, failure_type}]}`, `CompositeErrorHandler`. Backoff strategies:
  `ExponentialBackoffStrategy`, `ConstantBackoffStrategy`, `WaitTimeFromHeader` and
  `WaitUntilTimeFromHeader`.
- **Rate**: `HTTPAPIBudget {policies, ratelimit_reset_header, ratelimit_remaining_header,
  status_codes_for_ratelimit_hit}` with Fixed/Moving/Unlimited policies.
- **Transformations**: `AddFields`, `RemoveFields`, `KeysToLower`, `KeysToSnakeCase`, `KeysReplace`,
  `FlattenFields`, `DpathFlattenFields`, `RecordExpander`.
- **Schema**: `InlineSchemaLoader`, `JsonFileSchemaLoader`, and `DynamicSchemaLoader`, which derives
  the schema from a metadata endpoint with a `TypesMap`.
- **Validation**: `DpathValidator`, `PredicateValidator` and `ValidateAdheresToSchema`, used for spec
  `config_validations`.

**Interpolation.** Strings are Jinja in a `SandboxedEnvironment` (`interpolation/jinja.py`), with
`loopcontrols` the only extension. The variables are `config`, `stream_slice`/`stream_partition`/
`stream_interval`, `next_page_token`, `response`, `headers`, `record`, `last_record`, `parameters`,
`creation_response` and `polling_response`. The macros (`interpolation/macros.py`) are `now_utc`,
`today_utc`, `timestamp`, `max`, `min`, `day_delta`, `duration`, `format_datetime`,
`today_with_timezone`, `str_to_datetime`, `sanitize_url`, `camel_case_to_snake_case` and
`generate_uuid`.

### 3.3 A real one: `source-sentry` (manifest-only, `version: 6.60.12`)

`source-sentry` is a certified, manifest-only connector under ELv2, so its YAML is not reproduced
here (read it upstream:
https://github.com/airbytehq/airbyte/tree/master/airbyte-integrations/connectors/source-sentry).
What it shows, in outline: a `spec` for the connection settings, shared authenticator and
requester definitions reused by every stream through `$ref`s, a record selector, a paginator, a
datetime-cursor incremental sync on the streams that support it, and a `metadata.testedStreams`
block holding the Builder's test-read evidence. The schema-valid
example in §3.4 shows the same constructs, written for this report.

The `metadata.testedStreams` block is the Builder's evidence. Each stream was test-read, the PKs were
present and unique, and the responses succeeded. A `streamHash` ties that evidence to the exact stream
definition.

### 3.4 Full example connector (written for this report, schema-valid)

`airbyte/example/manifest.yaml`. It is validated with jsonschema against the CDK's
`declarative_component_schema.yaml` after `$ref` resolution (`scripts/validate_manifest.py`, 0 errors).
That check is stricter than the CDK's own, because it skips the type inference step. Running it with
the CDK was not possible here: installing the package was refused in this sandbox. The connector
covers OAuth client-credentials, cursor pagination, Retry-After backoff, error filters, a 30-day
windowed incremental with a 1-day lookback, a substream with incremental dependency, transformations,
an API budget, concurrency and a spec with a secret.

```yaml
version: 7.45.0
type: DeclarativeSource
description: Orders and their line items from a (fictional) ERP REST API, incremental on updated_at.
check: {type: CheckStream, stream_names: [customers]}

definitions:
  base_requester:
    type: HttpRequester
    url_base: "https://{{ config['host'] }}/api/v2/"
    http_method: GET
    authenticator:
      type: OAuthAuthenticator
      token_refresh_endpoint: "https://{{ config['host'] }}/oauth/token"
      grant_type: client_credentials
      client_id: "{{ config['client_id'] }}"
      client_secret: "{{ config['client_secret'] }}"
      scopes: [read:orders, read:customers]
    request_headers: {Accept: application/json}
    error_handler:
      type: DefaultErrorHandler
      max_retries: 5
      backoff_strategies:
        - {type: WaitTimeFromHeader, header: Retry-After}
        - {type: ExponentialBackoffStrategy, factor: 2}
      response_filters:
        - {type: HttpResponseFilter, action: FAIL, failure_type: config_error, http_codes: [401, 403],
           error_message: "The client credentials were refused; check client_id, client_secret and scopes."}
        - {type: HttpResponseFilter, action: IGNORE, http_codes: [404], error_message: "The order no longer exists; its lines are skipped."}
        - {type: HttpResponseFilter, action: RATE_LIMITED, http_codes: [429]}
  cursor_paginator:
    type: DefaultPaginator
    page_size_option: {type: RequestOption, inject_into: request_parameter, field_name: limit}
    page_token_option: {type: RequestOption, inject_into: request_parameter, field_name: cursor}
    pagination_strategy:
      type: CursorPagination
      page_size: 200
      cursor_value: "{{ response.get('next_cursor') }}"
      stop_condition: "{{ not response.get('next_cursor') }}"
  data_selector:
    type: RecordSelector
    extractor: {type: DpathExtractor, field_path: ["data"]}
  updated_at_cursor:
    type: DatetimeBasedCursor
    cursor_field: updated_at
    datetime_format: "%Y-%m-%dT%H:%M:%SZ"
    cursor_datetime_formats: ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"]
    start_datetime: {type: MinMaxDatetime, datetime: "{{ config['start_date'] }}", datetime_format: "%Y-%m-%dT%H:%M:%SZ"}
    end_datetime: {type: MinMaxDatetime, datetime: "{{ now_utc().strftime('%Y-%m-%dT%H:%M:%SZ') }}", datetime_format: "%Y-%m-%dT%H:%M:%SZ"}
    step: P30D
    cursor_granularity: PT1S
    lookback_window: P1D
    start_time_option: {type: RequestOption, inject_into: request_parameter, field_name: updated_since}
    end_time_option: {type: RequestOption, inject_into: request_parameter, field_name: updated_until}
  streams:
    customers:
      type: DeclarativeStream
      name: customers
      primary_key: ["id"]
      retriever:
        type: SimpleRetriever
        requester: {$ref: "#/definitions/base_requester", path: customers}
        paginator: {$ref: "#/definitions/cursor_paginator"}
        record_selector: {$ref: "#/definitions/data_selector"}
      schema_loader: {type: InlineSchemaLoader, schema: {$ref: "#/schemas/customers"}}
      transformations:
        - {type: RemoveFields, field_pointers: [["tax_code_raw"]]}
    orders:
      type: DeclarativeStream
      name: orders
      primary_key: ["id"]
      retriever:
        type: SimpleRetriever
        requester:
          $ref: "#/definitions/base_requester"
          path: orders
          request_parameters: {status: "{{ config.get('status', 'all') }}"}
        paginator: {$ref: "#/definitions/cursor_paginator"}
        record_selector: {$ref: "#/definitions/data_selector"}
      incremental_sync: {$ref: "#/definitions/updated_at_cursor"}
      schema_loader: {type: InlineSchemaLoader, schema: {$ref: "#/schemas/orders"}}
      transformations:
        - type: AddFields
          fields:
            - {type: AddedFieldDefinition, path: ["total_vnd"], value: "{{ record['total'] * record.get('fx_rate', 1) }}"}
    order_lines:
      type: DeclarativeStream
      name: order_lines
      primary_key: ["order_id", "line_no"]
      retriever:
        type: SimpleRetriever
        requester: {$ref: "#/definitions/base_requester", path: "orders/{{ stream_partition['order_id'] }}/lines"}
        paginator: {type: NoPagination}
        record_selector: {type: RecordSelector, extractor: {type: DpathExtractor, field_path: ["lines"]}}
        partition_router:
          type: SubstreamPartitionRouter
          parent_stream_configs:
            - type: ParentStreamConfig
              stream: {$ref: "#/definitions/streams/orders"}
              parent_key: id
              partition_field: order_id
              incremental_dependency: true
      transformations:
        - type: AddFields
          fields: [{type: AddedFieldDefinition, path: ["order_id"], value: "{{ stream_partition['order_id'] }}"}]
      schema_loader: {type: InlineSchemaLoader, schema: {$ref: "#/schemas/order_lines"}}

streams:
  - $ref: "#/definitions/streams/customers"
  - $ref: "#/definitions/streams/orders"
  - $ref: "#/definitions/streams/order_lines"

api_budget:
  type: HTTPAPIBudget
  ratelimit_reset_header: X-RateLimit-Reset
  ratelimit_remaining_header: X-RateLimit-Remaining
  status_codes_for_ratelimit_hit: [429]
  policies:
    - type: MovingWindowCallRatePolicy
      rates: [{type: Rate, limit: 100, interval: PT1M}]
      matchers: []

concurrency_level: {type: ConcurrencyLevel, default_concurrency: "{{ config.get('num_workers', 2) }}", max_concurrency: 8}

spec:
  type: Spec
  connection_specification:
    $schema: http://json-schema.org/draft-07/schema#
    type: object
    required: [host, client_id, client_secret, start_date]
    additionalProperties: true
    properties:
      host: {type: string, title: API host, order: 0}
      client_id: {type: string, title: Client ID, order: 1}
      client_secret: {type: string, title: Client secret, airbyte_secret: true, order: 2}
      start_date: {type: string, title: Start date, format: date-time,
                   pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", order: 3}
      status: {type: string, enum: [all, open, closed], default: all, order: 4}
      num_workers: {type: integer, minimum: 1, maximum: 8, default: 2, order: 5}

schemas:
  customers:   {type: object, additionalProperties: true, properties: {id: {type: string}, name: {type: ["null", string]}, email: {type: ["null", string]}, updated_at: {type: ["null", string], format: date-time}}}
  orders:      {type: object, additionalProperties: true, properties: {id: {type: string}, customer_id: {type: ["null", string]}, status: {type: ["null", string]}, total: {type: ["null", number]}, fx_rate: {type: ["null", number]}, total_vnd: {type: ["null", number]}, updated_at: {type: string, format: date-time}}}
  order_lines: {type: object, additionalProperties: true, properties: {order_id: {type: string}, line_no: {type: integer}, sku: {type: ["null", string]}, quantity: {type: ["null", number]}, unit_price: {type: ["null", number]}}}
```

The `orders` stream emits STATE `{"state_type":"date-range","slices":[{start,end,most_recent_cursor_value}]}`
after each 30-day window closes. `order_lines` emits the per-partition form with
`parent_state.orders`, so a resumed run re-reads only the orders changed since the last run and then
their lines.

### 3.5 Connector Builder backend and the manifest server

- `connector_builder/` is the backend of the Builder UI. The config carries
  `__injected_declarative_manifest` and `__command` (`test_read` | `resolve_manifest` |
  `full_resolve_manifest`). `__test_read_config` sets the limits `TestLimits(max_records=100,
  max_pages_per_slice=5, max_slices=5, max_streams=100)`. Retries and the HTTP cache are turned off
  under limits, and every request and response is logged back to the UI as auxiliary messages.
- `manifest_server/` is a FastAPI app (`pip install airbyte-cdk[manifest-server]`, `manifest-server
  start`) with `POST /v1/manifest/test_read`, `/check`, `/discover`, `/resolve` and
  `/full_resolve`, plus `capabilities` and `health`. It is the HTTP form of the Builder: edit YAML,
  test a stream, inspect pages, slices and state.
- The Builder UI has "AI assist" (`docs/platform/connector-development/connector-builder-ui/ai-assist.md`):
  an LLM drafts the auth, pagination and streams from API docs or an OpenAPI URL, and a person edits
  and test-reads the result.

---

## 4. Connector metadata, registry, support levels

Every connector directory has a `metadata.yaml` (`metadataSpecVersion: "1.0"`). For a
manifest-only connector such as `source-pokeapi` it names the connector's id and image, its
release stage and support level, its licence (ELv2 for almost all), the registries it is published
to (OSS, Cloud, PyPI on or off), its tags (language, CDK) and its documentation URL. Upstream:
https://github.com/airbytehq/airbyte/blob/master/airbyte-integrations/connectors/source-pokeapi/metadata.yaml
(ELv2: read there, not copied here).

`source-postgres` adds `allowedHosts: [${host}, ${tunnel_method.tunnel_host}]` (an egress allowlist
interpolated from config), `ab_internal: {ql: 400, sl: 300}` (internal quality and support scores),
`maxSecondsBetweenMessages: 7200` (a heartbeat timeout), `connectorIPCOptions` (socket/protobuf) and
`releases.breakingChanges: {"1.0.0": {message, upgradeDeadline, scopedImpact}}`.

The registry (`oss_registry.json`, ~6 MB) is the generated union of the metadata files. Each entry
also carries the full `spec`, `generated.metrics` (`sync_success_rate` and `usage` buckets per
cloud/oss), git and release provenance, and `ab_internal.isEnterprise`. A manifest-only connector is
"installed" by downloading
`https://connectors.airbyte.com/files/metadata/airbyte/{name}/{version}/manifest.yaml` and handing it
to the `source-declarative-manifest` image or to the CDK in-process.

Support levels (`docs/integrations/connector-support-levels.md`): **Airbyte** (certified: maintained
by Airbyte, production-ready, few breaking changes), **Enterprise** (extra licence), **Marketplace**
(community: no SLA, may break without notice, built mostly in the Builder), and **Custom**. Of 592
OSS sources only 72 are certified. The 509 manifest-only sources are almost all community.

---

## 5. Destinations: normalization → typing/deduping → direct-load

- The old "normalization" (dbt-generated SQL that unnested objects into sub-tables) is gone.
- **Destinations V2 / typing and deduping**: one stream maps to one final table. Raw JSON first lands
  in `airbyte_internal.raw_{namespace}__{stream}` (`_airbyte_raw_id`, `_airbyte_data`,
  `_airbyte_extracted_at`, `_airbyte_loaded_at`), then a T+D SQL step casts it into the final table.
  **A cast failure no longer fails the sync.** The value is nulled and recorded in
  `_airbyte_meta.changes = [{field, change: NULLED, reason: DESTINATION_TYPECAST_ERROR}]`. A failed
  sync means "could not *move* data", and bad content shows up per row.
- **Direct-load** (replacing T+D): the connector types values at insert time, so there is no
  persistent raw table. Dedup writes to a temp table during the sync and merges at the end.
  Overwrite builds a temp table and swaps it in (no data downtime). Schema evolution becomes
  `ALTER TABLE` and can fail on uncastable history.
- Metadata columns on every row: `_airbyte_raw_id` (UUID), `_airbyte_extracted_at`,
  `_airbyte_loaded_at`, `_airbyte_generation_id` (refresh generation), and `_airbyte_meta {sync_id,
  changes}`. CDC rows add `_ab_cdc_lsn`/`_ab_cdc_cursor`, `_ab_cdc_updated_at` and
  `_ab_cdc_deleted_at` (deletes arrive as records).
- The Python destination interface (`cdk/airbyte_cdk/destinations/destination.py`) is
  `write(config, configured_catalog, input_messages) -> Iterable[AirbyteMessage]`: you yield back
  each STATE once its records are durable. The serious destinations are Kotlin, on `airbyte-cdk/bulk`.

---

## 6. PyAirbyte (`pyairbyte/airbyte/…`, ELv2)

```python
import airbyte as ab
src = ab.get_source("source-sentry", config={...}, streams="*")   # sources/util.py:get_source
src.check()
for rec in src.get_records("issues"): ...                        # no cache, lazy iterator
result = src.read(cache=ab.get_default_cache())                  # DuckDB by default
result["issues"].to_pandas()
```

- `get_source` chooses an executor (`_executors/`) in this order: (1) a registry YAML manifest →
  `DeclarativeExecutor`, which runs `ConcurrentDeclarativeSource` **in-process**; (2) a PyPI package →
  a venv per connector, installed by pip/uv at runtime; (3) Docker. Also `local_executable`,
  `source_manifest=dict|Path|url`, and `no_executor` (spec only, from the registry).
- The registry comes from `connectors.airbyte.com/files/registries/v0/oss_registry.json`
  (`AIRBYTE_LOCAL_REGISTRY` overrides it). Manifests come from `…/metadata/airbyte/{name}/{version}/manifest.yaml`.
- Caches (`caches/`): DuckDB, Postgres, Snowflake, BigQuery and MotherDuck. The cache tables carry
  `_airbyte_raw_id`, `_airbyte_extracted_at` and `_airbyte_meta`, and **state and catalog are stored
  in the cache itself** (`_state_backend.py: SqlStateBackend`). So `read()` is incremental across runs.
  `WriteStrategy`: `merge` (needs a PK), `append`, `replace`, and `auto` (PK → merge, else cursor →
  append, else replace).
- `airbyte/mcp/` is an MCP server (FastMCP) with tools `validate_connector_config`,
  `list_source_streams`, `get_source_stream_json_schema`, `read_source_stream_records`,
  `get_stream_previews`, `sync_source_to_cache`, `run_sql_query` (guarded by `_is_safe_sql`) and
  `list_cached_streams`, plus cloud tools. `airbyte/agents/` (experimental, 2026) holds "Airbyte
  Agents" connectors: entity-level read and write actions, one call at a time.
- Airbyte Cloud's **context layer** (`docs/platform/context-layer/readme.md`) offers agents "direct
  reads": live, read-only queries of a source or destination outside a sync, via
  `execute_external_api_query` / `execute_external_sql_query` after reading a per-connector "skill" doc.
  Airbyte itself says: "it's possible for a user to see data they might not otherwise have access
  to". Direct reads use the connector's saved credentials, not the asker's permissions.

---

## 7. Where Data Agent stands (graph-located, lines read)

- `backend/services/worker_ingest/worker.py`: on `ingestion_job.created` / `job_run.backfill_requested`
  it reads the **whole** table (`SELECT * FROM schema.table`, asyncpg or `libs/source_drivers.read`) into
  memory and calls `da_materialize_upload(table, columns, rows, p_replace=true)` with every row in one
  JSONB argument. Then it writes `JobRun` counts and catalogs `Dataset`/`ColumnMeta`.
  `if job.mode != "full": raise ValueError("only full ingestion is wired; incremental and CDC need
  cursor adapters")`.
- `libs/dataagent_db/models/ingest.py`: `IngestionJob.mode` ("full, incremental, cdc"),
  `cursor_column`, `cursor_value`, `schedule_cron`, `freshness_sla`, `dlq_retention_days`. `JobRun`
  has `rows_read/written/dlq`, `cursor_from/to`, `lag_seconds` and `status`. There is a `DlqRecord`
  table. The schema already anticipates incremental; the runner does not use it.
- `catalog.Source.kind` already lists `rest_api`, `sftp`, `sharepoint`, `object_store` and
  `kafka_cdc` besides the SQL engines. `secret_ref` is an OpenBao path (BR-14).
- `libs/source_drivers/__init__.py`: MySQL, SQL Server and ClickHouse, with `(columns, rows)` results,
  TLS fail-closed, read-only transactions and credential-free `DriverError`s. PostgreSQL is reached
  through asyncpg by each caller.
- `services/worker_agent/providers/registry.py` + `base.py` + `relational.py`: `ProviderRegistry`
  (explicit, deterministic), the `DataProvider` protocol (`name`,
  `async execute(ProviderRequest) -> ResultEnvelope`), and one `RelationalProvider` delegating to
  `run_query` (scope checks, governed provenance with `query_run_id`). `ProviderRequest.sql` is a
  required `NonEmptyStr`: every provider is SQL-shaped today.
- `services/crawler/` handles web acquisition with a review gate (queued → fetching → extracting →
  in_review) and schema drift (`acquire.SchemaDrift`).
- `docs/research/agent-repos.md:805` already summarized Airbyte's spec/check/discover/read. No backlog
  item references it.

---

## 8. What Data Agent should take, module by module

### 8.1 worker_ingest: adopt the protocol's *semantics*, not its process boundary

1. **Per-stream opaque state with the commit rule.** Replace `IngestionJob.cursor_value` (a text
   high-water mark) with a JSON `state` blob shaped like `AirbyteStreamState`, and write it **in the
   same transaction as the batch it covers**. That is the destination-echo rule collapsed into one
   Postgres transaction, so a crashed run resumes from the last committed batch, never past a hole.
   `JobRun.cursor_from/to` become the state before and after the run, with `rows_read` taken from
   `sourceStats.recordCount`. This is the missing "cursor adapter", and it is what invariant
   "OCC instead of last-write-wins" wants for job state.
2. **Interval-set watermarks.** For any parallel or partitioned read, store completed `slices`
   `{start, end, most_recent_cursor_value}` and merge them the way `ConcurrentCursor` does, rather than
   a single max. It costs about 60 lines (`merge_intervals`) and removes the classic "parallel chunks
   finished out of order, the watermark skipped a gap" bug.
3. **Chunked, resumable full refresh.** Stop `SELECT *`-ing whole tables into one JSONB call. Read in
   keyset or CTID ranges (PostgreSQL `ctid`, SQL Server/MySQL PK ranges), materialize per chunk, and
   checkpoint per chunk. Full refresh becomes resumable, and a table swap at the end (Airbyte's
   overwrite) keeps no-downtime semantics. `da_materialize_upload` needs a staging-table + swap mode
   and a merge-on-PK mode, matching `WriteStrategy` replace/merge/append/auto.
4. **Incremental with lookback, cursor resolution, at-least-once + dedup.** Use
   `WHERE cursor > state - lookback ORDER BY cursor` with upsert on the PK. Adopt the protocol's
   cursor resolution order (source-defined, then configured, then default; none set means the job is
   refused at creation, not at run time).
5. **Per-row change records, not failed runs.** Adopt `_airbyte_meta.changes` semantics: a value that
   does not fit the workspace type is nulled, and the row's `changes` (`field`, `NULLED|TRUNCATED`,
   reason enum) goes to `DlqRecord`/`rows_dlq`. The run fails only when data cannot *move*. Today one
   bad cast fails the whole job (`_fail`). This also fits the trust label: a dataset with nulled cells
   carries that fact.
6. **Failure taxonomy.** Map errors to `config_error` (wrong credentials or a missing table: do not
   retry, tell the admin), `transient_error` (retry with backoff, count `consecutive_failures`) and
   `system_error` (alert). Stream statuses STARTED/RUNNING/COMPLETE/INCOMPLETE map onto `JobRun.status`.
   The CDK's HTTP status table (§2.4) is a ready default.
7. **CDC: keep it native.** Airbyte's DB CDC is Debezium inside Java connectors (GLOBAL state = WAL
   position + per-table snapshot state). The `kafka_cdc` kind plus our Kafka means Debezium → Kafka →
   worker_ingest, with the GLOBAL-state idea (one shared position, per-table snapshot flags) and the
   `_ab_cdc_deleted_at` convention for deletes. Do not run Airbyte's Java connectors.

### 8.2 A YAML low-code connector runner for API sources (`kind = rest_api`)

This is the big one. The CDK is MIT, `ConcurrentDeclarativeSource` runs in-process, and its input is
a dict.

```python
# sketch: worker_ingest/connectors.py (new), sync code run with asyncio.to_thread
source = ConcurrentDeclarativeSource(source_config=manifest, config=config, catalog=configured_catalog, state=state_msgs)
for msg in source.read(logger, config, configured_catalog, state_msgs):
    match msg.type:
        case Type.RECORD: batch[msg.record.stream].append(msg.record.data)
        case Type.STATE:  flush(batch[stream]); persist(state) in the same transaction   # the commit rule
        case Type.TRACE:  stream status / error(failure_type) -> JobRun
        case Type.LOG:    structured log (secrets already masked by the CDK)
        case Type.CONTROL: rotated OAuth token -> write back to OpenBao, never to Postgres
```

- **Config assembly.** Non-secret fields live in `Source.conn_options`. Fields the manifest's spec
  marks `airbyte_secret: true` come from `Source.secret_ref` (OpenBao) at run time and are never
  stored. The spec's JSON Schema drives the source form in the UI. This was already noted in
  `agent-repos.md`; the runner makes it real.
- **Discover → catalog.** `discover` returns `AirbyteStream{json_schema, primary key, default cursor}`,
  which maps directly to `catalog.Dataset` + `ColumnMeta` (JSON-schema type → `normalized_type`) and
  gives the semantic layer PK and cursor candidates.
- **Governance hooks the CDK gives for free.** Secret masking in logs; the manifest's `api_budget`
  and `concurrency_level` (cap these per org); `AIRBYTE_ENABLE_UNSAFE_CODE` left **unset**, so
  `components.py` connectors are refused and only pure YAML runs. Jinja runs in a sandbox. Egress:
  the CDK's private-IP filter only switches on with `DEPLOYMENT_MODE=cloud` and blocks *all* private
  ranges, which is wrong for on-prem customers whose ERP sits on 10.x. Enforce `allowedHosts` from
  the connector's metadata, interpolated from config, at the k8s NetworkPolicy or egress-proxy level
  instead.
- **Connector lifecycle = our approval flow.** A manifest is a versioned, immutable artefact
  (hash-pinned, like `streamHash`). The LLM may *draft* one from API docs (as the Builder's AI assist
  does), a deterministic validation runs (schema validation + a limited `test_read` with
  `TestLimits(100 records, 5 pages, 5 slices)` + the "PKs present and unique, responses successful"
  checks of `metadata.testedStreams`), and a person publishes it. That is the "LLM proposes,
  deterministic checks plus a human decide" invariant, applied to connectors. The CDK's
  `manifest_server` routes (`test_read/check/discover/resolve`) are a ready backend for an authoring
  screen. Mount the same functions in the api module rather than running another service, since
  CLAUDE.md §7 says a new queue consumer goes into the worker that owns the schema.

### 8.3 `libs/source_drivers`: borrow the catalog shape and the chunked read

Airbyte's DB connectors are Java and add nothing for query-in-place. Take two ideas only. (a) A
`discover(source) -> list[StreamDescriptor + json_schema + pk + cursor candidates]` per engine
(information_schema queries), so worker_ingest and the schema scan share one catalog shape with the
API runner. (b) A `read_chunks(source, table, after=key, limit=n)` keyset reader returning
`(columns, rows, next_key)`, which item 8.1-3 needs. Keep the SQL guard, read-only transactions and
TLS rules exactly as they are. They are stricter than Airbyte's.

### 8.4 `providers/registry.py`: a governed "direct read" provider

Airbyte's context layer is the same idea as our query-in-place invariant, but with weaker permission
semantics: it reads with the connector's credentials. We can do better with an `ApiStreamProvider`:

- Generalize `ProviderRequest`: today `sql: NonEmptyStr` is mandatory. Make the operation a tagged
  union (`SqlOperation{sql}` | `StreamReadOperation{stream, filters, limit}`) so the registry holds
  non-SQL providers without overloading `sql`.
- `execute` runs a manifest stream with `TestLimits`-style caps (records, pages, slices) and
  `disable_cache`, maps records to `ResultEnvelope.rows`, sets `truncated` when a cap is hit, and
  records a `query_run`-equivalent provenance row with `trust="external"` (not "governed"). The
  number then carries its origin and the answer's trust label can say so.
- Permission is checked **before** the call (invariant 13): the stream must be exposed to the Data
  Space and the principal, and row-level rules apply to the returned rows, the way `run_query` does
  for SQL.
- Registration stays explicit: one provider instance per published manifest version. That fits the
  OpenBB-style plan's "explicit and built-in first" rule.

### 8.5 PyAirbyte for "300+ sources": no as a runtime, yes as a reference

- It is **ELv2**: it cannot be offered "as a hosted or managed service" exposing its features. For a
  product we ship or host, that needs legal review before it goes into the runtime. The CDK (MIT) is
  enough to run the 509 manifest-only sources in-process, since that is all `DeclarativeExecutor`
  does anyway.
- Its other executors install pip packages or pull Docker images **at run time** from the public
  internet. That clashes with an air-gapped enterprise k8s deploy, image pinning, and the "Ansible is
  the only deploy path" rule.
- The **connector manifests are themselves mostly ELv2** (553/592). Vendoring a chosen few is a
  licensing decision, not a technical one. The low-risk path is to write our own manifests for the
  APIs customers need (the Builder format makes that a day's work each) and use Airbyte's as reading
  material.
- Worth copying from PyAirbyte: state and catalog kept *inside the cache database*, `WriteStrategy.auto`,
  the MCP tool set as a checklist for our agent's data-source tools, and `get_samples`/`get_stream_previews`
  (a 10-record preview per stream) for the source-onboarding UX.

---

## 9. Ranked: the 8 most valuable things to adopt

| # | What | Where | Effort |
| --- | --- | --- | --- |
| 1 | **Per-stream opaque state, committed in the same transaction as its batch**, plus `sourceStats.recordCount` → `JobRun`. Unblocks `mode = incremental` (the `ValueError` in `worker.py`) | worker_ingest, `ingest.IngestionJob/JobRun` (migration v3_0022+) | M |
| 2 | **Chunked, resumable reads + write strategies** (keyset/CTID chunks; staging + swap for replace; merge on PK; append), replacing the single all-rows JSONB call | worker_ingest, `da_materialize_upload` routine, `libs/source_drivers.read_chunks` | M |
| 3 | **Declarative YAML connector runner** on the MIT CDK, in-process, for `kind = rest_api`, with OpenBao-filled `airbyte_secret` fields, unsafe code off, and per-org concurrency/budget caps | worker_ingest (new module), `requirements.txt` pin `airbyte-cdk==7.45.*` | L |
| 4 | **Spec-driven source forms + discover-to-catalog**: JSON-Schema `connectionSpecification` renders the form and validates config; `AirbyteStream` → `Dataset`/`ColumnMeta` with PK and cursor candidates | api ingest/catalog modules, frontend source setup | M |
| 5 | **Row-level change records instead of failed runs** (`NULLED/TRUNCATED` + reason enum into `DlqRecord`; the run fails only when data cannot move) and the **failure taxonomy** config/transient/system with retry policy | worker_ingest, DLQ, `consecutive_failures` | S |
| 6 | **Connector authoring with LLM draft + deterministic test-read + human publish**: manifest versions hash-pinned, `testedStreams`-style evidence stored, `TestLimits` caps | api module (manifest_server routes as library calls), approval flow | L |
| 7 | **Governed direct-read `ApiStreamProvider`**: generalize `ProviderRequest` beyond SQL, enforce permissions before the call, attach `trust="external"` provenance and truncation | `libs/contracts/v1/provider.py`, worker_agent `providers/` | M |
| 8 | **Interval-set watermarks + lookback window** (`merge_intervals`; per-partition state with parent state for substreams) | worker_ingest state module (shared by DB and API runs) | S |

**Skip:**

- **The Airbyte platform** (Temporal, workload launcher, per-sync pods, the orchestrator ↔ connector
  STDIO/socket split). We are one product with in-process workers and an outbox. A process boundary
  per sync adds cost without a tenant-isolation win we lack.
- **Java/Kotlin DB connectors and Airbyte's CDC.** Keep `libs/source_drivers` (stricter TLS and
  read-only rules) and do CDC through Debezium → our Kafka if a customer needs it.
- **PyAirbyte as a runtime dependency** (ELv2, run-time pip/Docker installs, public-internet
  registry). Read it, do not ship it.
- **Bulk-vendoring the 500+ community manifests.** They are ELv2, uncertified (520 of 592 sources
  are `community`), and a support burden. Pick a handful by customer demand and own them.
- **Typing-and-deduping raw tables.** Airbyte itself is replacing them with direct-load. Our workspace
  already types at materialization, so take only `_airbyte_meta` and the temp-table swap.
- **`components.py` custom code and `AIRBYTE_ENABLE_UNSAFE_CODE`.** Arbitrary Python from a manifest
  breaks the review model. YAML-only, or a reviewed Python provider in our own code.
- **Airbyte's context-layer permission model.** It reads with connector credentials. Ours checks the
  asker's permissions before generation and must stay that way.

---

### Files produced

- `research3/AIRBYTE-DEEP.md` (this report)
- `research3/airbyte/example/manifest.yaml`: the example connector of §3.4
- `research3/scripts/validate_manifest.py`: the `$ref` resolver + jsonschema check used on it
- `research3/airbyte/raw/`: `oss_registry.json`, Sentry/PokeAPI/exchange-rates manifests, PokeAPI/Postgres metadata
- Clones: `research3/airbyte/{cdk,protocol,pyairbyte,main}`. `main` is a sparse, depth-1 blobless clone; see §0
