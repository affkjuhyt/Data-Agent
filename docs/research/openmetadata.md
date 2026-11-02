# OpenMetadata, read in depth: what it models, how, and what our data agent should take

Researched 03/11/2026 against `open-metadata/OpenMetadata` `main` @ `d77e1fe709b4` (2 Nov 2026,
"Fixes #36556: Record the asset's change event when an approved task changes it").

**How the repository was obtained.** A full `git clone` was started and kept running in the
background at `research3/openmetadata/OpenMetadata/`. The link here was slow, about 0.6 MB/s, and
the pack was still downloading when this was written (more than 600 MB received after 18 minutes;
the repository is several GB with all its history). So the reading was done on a second clone,
`research3/openmetadata/om-sparse/`, made with `--filter=blob:none --depth 1`. Its sparse checkout
holds `openmetadata-spec/…/json/schema/` (1,115 JSON schemas, 904 of them entity and type schemas
per the repo's ARCHITECTURE.md), `ingestion/src/`, `openmetadata-service/src/main/{java,resources}`,
`openmetadata-mcp/`, `ARCHITECTURE.md`, `openspec/` and the incident-manager ADR. The UI was read
from the git tree listing only (pages and components), not file by file.

**Licence, which matters before we copy anything.** The root `LICENSE` is Apache-2.0, and that
covers the spec, the service and the UI. But `ingestion/LICENSE` is the **Collate Community License
1.0**, and 1,213 ingestion files carry its header. It forbids an "Excluded Purpose": *"making
available any software-as-a-service … that competes with Collate products"*. We sell a SaaS-capable
data product, so we take ideas, schema shapes and algorithms described in prose from the ingestion
code, never the code itself. Apache code from the spec and service is safe to adapt, with
attribution.

---

## 0. One-paragraph verdict

OpenMetadata is a schema-first metadata platform: JSON Schema is the source of truth, and the Java,
Python and TypeScript models are generated from it. It has one Java service (Dropwizard/JDBI over
MySQL/Postgres, plus Elasticsearch/OpenSearch), about 97 Python connectors that push metadata through
the same REST API, and a React SPA. It is very broad: every asset type, ownership, tags, glossary,
lineage, profiling, data quality, contracts, domains and data products, policies, and Flowable-based
governance workflows. Since 2025–2026 it also has a real **agent-facing layer**: an `AIContext`
envelope per asset, concept bindings for glossary terms and metrics, "context memories" that an agent
learns and a reviewer approves, and an MCP server with 21 tools. Our semantic layer is already stronger
than theirs where it counts for answering questions. We version metrics with impact preview, run an
eval overlay before certification, use two-person approval, and have a compiler. Their `metric` is a
descriptive record with an expression string, not an executable definition. What we should take is
everything **around** the number: provenance on every label, typed bindings from glossary terms to
columns and metrics, column-level lineage mined from query logs, a DQ incident lifecycle, data
contracts with SLA validation, per-field change requests with base values, and above all their habit
of handing an agent **staleness and quality signals next to each piece of context**, so the answer
can be qualified.

---

## 1. The spine: entity envelope, references, versioning, events

**Envelope.** Almost every entity implements `EntityInterface<EntityStatus>` and has the same fields:
`id` (uuid), `name`, `fullyQualifiedName` (FQN, dot-joined and quoted per part, parsed by an ANTLR
grammar), `displayName`, `description` (markdown), `owners` (users or teams), `reviewers`, `experts`,
`tags[]` (TagLabel), `domains[]`, `dataProducts[]`, `dataContract`, `certification`, `entityStatus`,
`version`, `changeDescription`, `incrementalChangeDescription`, `extension` (custom properties),
`deleted` (soft delete), `votes`, `followers`, and `provider` (`system`, `user` or `automation`).

**Relationships live in one table.** `entity_relationship(fromId, toId, fromEntity, toEntity,
relation, json)` is typed by the `Relationship` enum: `contains, createdBy, repliedTo, isAbout,
addressedTo, mentionedIn, testedBy, uses, owns, parentOf, has, follows, joinedWith, upstream,
appliedTo, relatedTo, reviews, reactedTo, voted, expert, editedBy, defaultsTo, relatesTo, inputPort,
outputPort, assignedTo, mappedTo, derivedFrom`. Lineage is just `relation = upstream` with
`lineageDetails` in the `json` column (`LineageRepository.addLineage`). The repository also rolls
lineage up to service, domain and data-product level (`insertLineage(fromDomain, toDomain, …)`).
Tags are a separate `tag_usage` table keyed by the target FQN, with `labelType` and `state`.

**Versioning** (`type/entityHistory.json`). `version` is a number in `Major.Minor` form that starts
at `0.1`. A backward-compatible change bumps the minor part. A breaking change bumps the major part:
the column-level updaters set `majorVersionChange = true` when a column is removed, a `dataLength`
or `precision` or `scale` shrinks, or a type changes (`EntityRepository.java` ~L13100). Every
version stores a `changeDescription {fieldsAdded[], fieldsUpdated[], fieldsDeleted[],
previousVersion, changeSummary}`, where each `fieldChange` is `{name, oldValue, newValue}` as JSON
strings. Full snapshots go to `entity_extension` under `<type>.version.<n>`, so any version can be
read back (`GET /v1/<type>/{id}/versions/{v}`).

**Session consolidation** (`EntityUpdater`). If the same user edits the same entity again within 10
minutes (`sessionTimeoutMillis`), the server reverts to the previous version and re-applies the
combined change, so edits made in one sitting become a single version. The consolidation is skipped
in three cases: `If-Match`/ETag optimistic locking is used, a rename happened, or the FQN changed
during the session. Optimistic locking is opt-in through `If-Match`; without it the API is
last-write-wins with consolidation.

**Change events** (`type/changeEvent.json`). After every write the service records `{eventType,
entityType, entityId, entityFullyQualifiedName, domains, previousVersion, currentVersion, userName,
impersonatedBy, timestamp, changeDescription, entity}`. The `eventType` values are `entityCreated,
entityUpdated, entityFieldsChanged, entityNoChange, entitySoftDeleted, entityDeleted,
entityRestored, threadCreated, …, entityLineageAdded, entityLineageDeleted, entityLineageUpdated,
ontologyImported, …`. The events go to a `change_event` table that alert subscriptions read
(subscription types `Webhook, Slack, MsTeams, GChat, Email, ActivityFeed,
GovernanceWorkflowChangeEvent`), and they also feed the governance-workflow triggers and
the search reindex. `impersonatedBy` records a bot acting for a user, so audit knows who the effective
human was.

**Custom properties.** An admin adds typed properties to an entity type: string, markdown,
integer, number, date, dateTime and time (stored as `date-cp`, `dateTime-cp`, `time-cp`), duration,
email, timestamp, enum, entity reference(s), table (`table-cp`), time interval, SQL query and
hyperlink. The values live in `extension`. They are versioned and
searchable like core fields.

---

## 2. Assets: table, column, profile, query, usage

`entity/data/table.json` (1,368 lines) is the centre of the model. A table has `tableType`
(`Regular, External, View, MaterializedView, Iceberg, DeltaLake, Partitioned, Foreign, Stream,
SemanticView, …`), `columns[]`, `tableConstraints[]` (`UNIQUE, PRIMARY_KEY, FOREIGN_KEY` with
`referredColumns`, plus sort, dist and cluster keys), `tablePartition`, `schemaDefinition` (DDL),
`dataModel` (the dbt or DDL model that produces it, with its SQL), `joins` (observed joins),
`usageSummary`, `profile`, `tableProfilerConfig`, `customMetrics`, `sampleData`, `testSuite`,
`lifeCycle` (created, updated and accessed timestamps), `certification`, `retentionPeriod`,
`sourceHash` (to skip unchanged assets on re-ingestion), `processedLineage` and `queries`.

A **column** has `name, displayName, dataType, arrayDataType, dataLength, precision, scale,
dataTypeDisplay, description, fullyQualifiedName, tags, constraint, ordinalPosition, jsonSchema,
children` (nested struct columns), `profile, customMetrics, extension`.

A **column profile** is time-series data, not versioned: `valuesCount, nullCount, nullProportion,
uniqueCount, uniqueProportion, distinctCount, distinctProportion, min, max, minLength, maxLength,
mean, sum, stddev, variance, median, firstQuartile, thirdQuartile, interQuartileRange,
nonParametricSkew, histogram, cardinalityDistribution, customMetrics`. A **table profile** has
`rowCount, columnCount, sizeInByte, createDateTime, profileSample, profileSampleType`, and system
profiles add DML operations. Profiles can be sampled (`PERCENTAGE` or `ROWS`, with a sampling method
and partition-aware config). The profiler metric registry (`ingestion/src/metadata/profiler/
metrics/{static,composed,window,system,hybrid}`) holds metrics such as `row_count`, `null_count`,
`distinct_count`, `unique_count`, `regexp_match_count`, `stddev`, and composed ratios.

A **query** (`entity/data/query.json`) is a first-class entity: `query` (the SQL), `checksum`
(dedup), `duration`, `users`, `usedBy` (users unknown to the catalog), `queryDate`, `queryUsedIn`,
`triggeredBy` (a stored procedure or pipeline task), `processedLineage`, `exclude_usage` and `tags`.

**Usage** (`type/usageDetails.json`) is `{dailyStats, weeklyStats, monthlyStats}`, each
`{count, percentileRank}`. It is computed per day from query logs, and the percentile rank is
relative to all assets on that day.

**Joins** (`table.json#/definitions/tableJoins`) are `{startDate, dayCount, columnJoins
[{columnName, joinedWith[{fullyQualifiedName, joinCount}]}], directTableJoins}`. They are mined
from the same query logs, and they become the AIContext `frequentJoins` hints described in §8.

## 3. Ingestion: connectors, workflows, query-log lineage and usage

*Concepts only: this code is under the Collate licence.*

**Workflows.** `ingestion/src/metadata/workflow/` holds one workflow class per job: `metadata.py`,
`usage.py`, `profiler.py`, `data_quality.py`, `classification.py` (auto-classification) and
`application.py`. Each runs Source → Processor/Stage → Sink. The sink always calls the REST API
(`sink/metadata_rest.py`), so ingestion holds no privileged database access. Workflows are YAML
configs that Airflow or a K8s operator schedules as "ingestion pipelines" bound to a service. A
connector is a plugin: `service_spec.py` plus `connection.py` plus `metadata.py`, and a `<Name>Source`
that yields entities through a topology of producers and processors. The dbt connector reads
`manifest.json` and `catalog.json` and attaches the model SQL, tests (as test cases), descriptions,
owners and tags to tables. There are semantic-layer paths too (`semantic_metrics.py`,
`semantic_metric_lineage.py`): they turn Snowflake semantic views and Databricks metric views into
`Metric` entities. The name is `<service>-<digest>` over the quoted-part identity, the type comes from
the aggregation head (`SUM → MetricType.SUM`), the dimensions are classified TIME or CATEGORICAL,
and a table→metric lineage edge is added.

**Query logs → lineage and usage.** Each warehouse connector has a `queries.py` that reads its query
history. On Postgres this is `pg_stat_statements` joined with `pg_database` and `pg_user`. The reader
filters out OpenMetadata's own and dbt's tagged queries, which carry comments like
`/* {"app": "OpenMetadata", …} */`. `ingestion/lineage/parser.py` (`LineageParser`) wraps
`collate-sqllineage`, which has three back-ends: **sqlglot**, sqlfluff and sqlparse. It tries them
in order of fidelity under a 30-second timeout per query and keeps the best result
(`_evaluate_best_parser`). It extracts `source_tables`, `target_tables`, `intermediate_tables`,
`column_lineage` as (from, to) column pairs, and `table_joins` from comparison predicates.
`masker.py` replaces literals with `?` before a query is stored or logged, so PII in WHERE clauses
does not leak into the catalog. `get_query_hash` deduplicates queries. Lineage edges are written with
`lineageDetails.source = QueryLineage` (or `ViewLineage`, `DbtLineage`, `PipelineLineage`,
`OpenLineage`, …) and the SQL that produced them. The usage stage aggregates `TableUsageCount
{table, date, count, sqlQueries, joins}` per day and sends it in bulk.

**Auto-classification (PII).** `pii/` has a `TagProcessor` with column-name scanners and content
scanners (spaCy NER models en, es, it, pl … plus Presidio recognizers), `tag_scoring`, and a
`conflict_resolver`. Recognizers are configured **on the Tag** (`tag.recognizers[]`,
`autoClassificationEnabled`, `autoClassificationPriority`). A recognizer has `target: content |
column_name`, `confidenceThreshold`, and an `exceptionList` of entity links where it must not run,
fed by user feedback. Automated labels are written with `labelType: Automated`, `state: Suggested`,
a `reason` ("why this tag was proposed"), `appliedBy`, and `metadata` naming the recognizer and its
score. When an owner rejects a label, the `RecognizerFeedbackReviewWorkflow` runs and, if the
rejection is approved, the entity link goes into that recognizer's exception list.

## 4. Glossary, tags, classification, certification, tiers

**Classification → Tag** is a hierarchy (`Classification.tag1.tag2`). `mutuallyExclusive` on a
classification or tag means an entity can carry only one tag of the group (Tier, Certification, PII
sensitivity). The system seeds are: `PII {None, NonSensitive, Sensitive}` with recognizers;
`PersonalData`; **Tier1–Tier5**, defined by business criticality (Tier1 "critical source of truth …
used in critical metrics and dashboards", Tier5 "private/unused … no usage in the last 60 days");
and **Certification {Bronze, Silver, Gold}**. `assetCertification` is `{tagLabel, appliedDate,
expiryDate}`, so **a certification expires**.

**TagLabel** (appendix) is the most reusable idea in the whole model. Every tag or glossary term
attached anywhere records:
- `source`: `Classification` or `Glossary`;
- `labelType`: `Manual`, `Propagated` (came from upstream through lineage), `Automated` (a tool),
  `Derived` (implied by a glossary term's associated tag), or `Generated` (AI);
- `state`: `Suggested` or `Confirmed`. The owner must confirm a suggestion before it counts;
- `reason`, `appliedAt`, `appliedBy`, `metadata` (recognizer info).

**Glossary → GlossaryTerm** (full schema in the appendix). A term has `synonyms[]`, `parent` and
`children` (FQN `glossary.parent.child`), `relatedTerms[]` (now typed `termRelation`
`{relationType: broader|narrower|synonym|relatedTo|…, provenance: Manual|Inferred|Imported|
AiSuggested, status}`), `references[]` (external glossaries), `conceptMappings[]` (SKOS
`EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, …`), `iri` for ontology round-trips, `reviewers`, `owners`,
`entityStatus` (`Draft, In Review, Approved, Archived, Deprecated, Rejected, Unprocessed`), and
`mutuallyExclusive` (children are alternatives). The term's **`tags`** are *associated tags*: in
the schema's own example, a term `User.PhoneNumber` carries `PII.Sensitive`, so any column labelled
with that term gets `PII.Sensitive` automatically as a `Derived` label. That chain, business term →
classification → access policy, is how a steward's glossary work becomes masking.

A glossary term is **applied** to columns, tables and dashboards as a TagLabel with
`source = Glossary`. That is how OpenMetadata binds a business concept to physical columns. The new
`conceptContext` (§8) reads these bindings back for an agent.

## 5. Metric, data product, domain

**Metric** (appendix). `metricExpression {language: SQL|Java|JavaScript|Python|External, code}`,
`metricType` (`COUNT, SUM, AVERAGE, RATIO, PERCENTAGE, …, CUMULATIVE, DERIVED, CONVERSION`),
`unitOfMeasurement`, `granularity` (SECOND…YEAR), `dimensions[] {name, type: CATEGORICAL|TIME,
expression}`, `measures[] {name, aggregation, expression}`, `filters[] {where}`, `relatedMetrics`,
`parent` and `children` (variants), `metricGroup`, `assets` (the tables and columns it is computed
on, a first-class metric→asset link), owners, experts, reviewers, `certification`, `entityStatus`,
and `derivedFrom` (the context memory the Memory Agent derived it from). What it **lacks**, and we
have: an executable compiler, version-pinned definitions with `valid_from`/`valid_to`, a filed-report
lock, additivity, period semantics, and a time dimension per version. Their metric versions are
metadata versions (0.1, 0.2, …), not definition versions that answers pin to.

**Domain** is a bounded context: `domainType: Source-aligned | Consumer-aligned | Aggregate`,
nested (`parent` and `children`), with `owners`, `experts` and `assets`. The seeded
`DomainOnlyAccessPolicy` denies everything when `!hasDomain()`: a user sees only the assets of their
own domains.

**DataProduct** (appendix). It is aligned with ODPS v4.1. It has `lifecycleStage` (`IDEATION →
DESIGN → DEVELOPMENT → TESTING → PRODUCTION → DEPRECATED → RETIRED`, "driven by the governance
workflow, not directly user-editable"), `dataProductType` (`DATASET, REPORTS, ANALYTIC_VIEW,
DECISION_SUPPORT, …`), `visibility` (`PRIVATE → INVITATION → ORGANISATION → DATASPACE → PUBLIC`;
"promotion to more open levels should go through the governance workflow"), `portfolioPriority`,
input and output **ports** `{portType, protocol, format, endpoint, dataAsset}`, `sla {tier:
GOLD|SILVER|BRONZE, availability %, responseTime ms, dataFreshness minutes, dataQuality score}`,
`consumesFrom` and `providesTo` (product-to-product lineage), `assets` (now paginated through
`/dataProducts/{id}/assets`), `owners`, `experts`, `reviewers`, `domains`, `certification`.
A product can hold an approved **data contract** that its assets inherit (§6).

## 6. Data contracts

`entity/data/dataContract.json` (appendix) is attached to one `entity` (a table, topic, …) or to a
data product, and assets then inherit it (`inherited: true`). It has five sections:

1. `schema[]`: expected columns, using the same `column` shape as `table.json`.
2. `semantics[]`: `semanticsRule {name, description, rule (JSONLogic), enabled, entityType,
   ignoredEntities, inherited}`. These rules run against the asset's **metadata**, for example
   "has an owner" or "description not empty" (`RuleEngine.evaluateAndReturn(entity, rules)` in
   `DataContractRepository.validateSemantics`). They are not rules on the data.
3. `qualityExpectations[]`: references to test cases, grouped into a contract `testSuite`.
4. `sla {refreshFrequency {interval, unit}, maxLatency {value, unit}, availabilityTime "09:00",
   timezone, retention, columnName}`. `columnName` is the column whose newest value is the refresh
   time.
5. `security {dataClassification, policies[{accessPolicy, identities[], rowFilters[{columnName,
   values[]}]}]}`, plus `termsOfUse.content`, described as *"for both human and AI agents
   consumption"*.

It also has `entityStatus` (`Draft, Active, Deprecated` in the contract's own enum; approval goes
through the generic status), `effectiveFrom`/`effectiveUntil`, `contractUpdates[]`, `reviewers`,
and import/export to **ODCS** (Open Data Contract Standard; the `odcsQualityRules` and
`odcsElementExtensions` fields keep round-trips lossless).

**Validation** (`DataContractRepository.validateContract`). It first aborts any running validation
and writes a `Running` result. Then it validates the schema against the live entity (`failedFields`,
`duplicateFields`, `typeMismatchFields: "fieldName: expected T1, got T2"`), evaluates the semantics
rules (passed, failed, skipped, total, failedRules), and validates the SLA (`refreshFrequencyMet`,
`latencyMet`, `availabilityMet`, `actualLatency`, `lastRefreshedAt`, and `refreshedAtSource`, which
says whether the time came from the SLA column's profile max, the system write metrics, or a
lifecycle timestamp). If there are quality expectations, it deploys a DQ pipeline and stays
`Running` until the test results arrive. The `DataContractResult` time series is `{contractExecution
Status: Running|Success|Failed|PartialSuccess|Aborted|Queued, schemaValidation,
semanticsValidation, qualityValidation {passed, failed, total, qualityScore 0–100}, slaValidation,
incidentId, executionTime}`. A failed contract opens an incident. `latestResult` is denormalised
onto the contract, so the UI shows a contract badge on every asset.

## 7. Data quality: test definitions, cases, suites, results, incidents

- **TestDefinition**: a reusable template. It has `entityType` (TABLE or COLUMN), `testPlatforms`
  (`OpenMetadata, GreatExpectations, dbt, Deequ, Soda, Other`), `supportedDataTypes`,
  `parameterDefinition[]`, a default `dataQualityDimension`, `supportsRowLevelPassedFailed`,
  `supportsDynamicAssertion`, `sqlExpression` (a Jinja2 template for custom SQL definitions; this is
  the "rule library"), `validatorClass`, `supportedServices` and `enabled`. Built-in validators
  include `columnValuesToBeBetween`, `…ToBeNotNull`, `…ToBeUnique`, `…ToBeInSet` and `…NotInSet`,
  `…ToMatchRegex`, `columnValuesMissingCount`, `columnValuesSumToBeBetween`,
  `columnValueMean/Median/StdDev/Min/MaxToBeBetween`, `columnValueLengthsToBeBetween`,
  `columnValuesToBeAtExpectedLocation`, `tableRowCountToBeBetween` and `…ToEqual`,
  `tableRowInsertedCountToBeBetween` (freshness and volume), `tableColumnCount…`,
  `tableColumnNameToExist`, `tableColumnToMatchSet`, `tableCustomSQLQuery`, and the RuleLibrary SQL
  validators.
- **DataQualityDimension**: a first-class entity. The system set seeded in
  `json/data/dataQualityDimension/` (Accuracy, Completeness, Consistency, Integrity, SQL,
  Uniqueness, Validity) cannot be edited, and users can add
  their own. Renaming a dimension updates it everywhere because it is a relationship.
- **TestCase** (appendix): `testDefinition` + `entityLink` (`<#E::table::fqn::columns::col>`) +
  `parameterValues[]`, a `testSuite` (the basic suite of the table) and `testSuites[]` (logical
  suites), `computePassedFailedRowCount`, `useDynamicAssertion` (learned bounds, which give
  `predictedValue` in the results), `dimensionColumns[]` + `topDimensions` (run the test grouped
  by, say, `region`, and rank the slices by impact score), `inspectionQuery` (SQL that returns the
  failing rows), `failedRowsSample`, `autoCloseIncident`, and `incidentId` with `incidentStatus`.
- **TestCaseResult** (time series): `testCaseStatus` (`Success, Failed, Aborted, Queued`),
  `result` text, `testResultValue[] {name, value, predictedValue}`, `passedRows`, `failedRows` and
  their percentages, `minBound` and `maxBound`, `incidentId`, and dimension results.
- **Impact score** (`data_quality/validations/impact_score.py`, prose only): `impact =
  failure_rate² × volume_factor × min(1, n/100) / 1.5`. The volume factor goes in tiers from 0.25 for
  fewer than 10 rows to 1.5 for 100k rows or more. It ranks which dimension slice is really broken,
  so one bad row in a tiny group does not outrank 9,000 bad rows.
- **TestSuite**: *basic* (one per table, `basicEntityReference`) or *logical* (any test cases
  grouped for a purpose, for example a contract's). It has a `summary` and `testCaseResultSummary`.
- **Incident manager** (`testCaseResolutionStatus.json`): one open incident per test case
  (`getOrCreateIncident`). The `stateId` sequence goes through `New → Ack → Assigned → Resolved`,
  with `severity` (Severity1–5), `testCaseResolutionStatusDetails` (assignee, or the resolved reason
  and comment), and a denormalised `failureSummary`. The Feb-2026 ADR
  (`adr-incident-manager-governance-workflows.md`) moves this switch statement into a governance
  workflow "Task Lifecycle Node". That adds auto-assign on create (for example to the table owner),
  auto-close when the next run passes (`AutoResolved`), TTL expiry (`Expired`), and hooks on any
  transition. The ADR sizes it for 10K–75K concurrent open incidents. There is also a
  `root_cause_analysis` MCP tool that walks upstream lineage for failing tests, then downstream for
  impact.

## 8. AI and agent pieces (the part most relevant to us)

1. **`AIContext` / Context Profile** (`type/aiContext.json`, `service/aicontext/AIContextBuilder.
   java`). This is one call that returns everything an agent needs about an asset, as JSON or as
   OKF-style markdown (YAML frontmatter, then `# Schema` table, keys, joins, business definitions,
   articles, lineage). It contains identity, owners, tags, approved `glossaryTerms` (asset-level, and
   column-level on each `fieldContext`), `metrics`, `articles`, immediate `upstream` and `downstream`
   plus edges with column mappings, and `assetContext.table {columns, primaryKey, foreignKeys,
   frequentJoins[{column, joinedWith, joinCount}], partitionColumns, schemaDefinition, dataModel,
   sampleData (≤ 10 rows, PII-masked, permission-filtered)}`. It also has `observability {rowCount,
   profiledAt, profileSample, columnProfiles[{nullProportion, uniqueProportion, distinctCount, min,
   max, cardinalityDistribution}], dataQuality {total, passed, failed, aborted, openIncidents}}`,
   which is documented as *"so an agent can caveat its answer when tests are failing or an incident
   is open"* and is kept out of the semantic embedding because it is volatile. A `fingerprint` hash
   detects staleness. Each `knowledgeItem` has **`stale` + `staleReasons[]`** (`assetUpdated`: the
   asset changed after the knowledge was last updated; `dataQualityFailing`), and `contentTruncated`
   tells the agent to fetch the full text with a named tool. A `BudgetTracker` caps the whole envelope
   to a token budget.
2. **Concept context** (`get_concept_context`). It resolves a glossary term or metric to its
   definition, synonyms, related terms, **bindings** (each asset and column that realises the
   concept, with its permission-masked profile and up to 10 sample values), related metrics, and
   **evidence**: saved `Query` entities linked through source memories, with `lastRunAt` and
   `lastRunStatus` ("absence means unknown, not successful"). Caps are explicit (10 assets, 25
   bindings each, a scan stopping at 500), and `totalAssets`/`totalBindings` are documented as
   *lower bounds* when `truncated`. `MetricColumnResolver` binds the identifiers of a metric's SQL to
   the applied tables' columns "conservatively, excluding ambiguous columns". It encodes the scoping
   rule that only ORDER BY resolves a bare name to an output alias.
3. **Context memories** (`entity/context/contextMemory.json`). These are reusable learnings
   (`memoryType: Preference, UseCase, Note, Runbook, Faq, Learning`, where a Learning is "something
   the agent had to discover in a conversation, e.g. a failed query and its fix"), scoped
   `UserGlobal` or `EntityScoped`, with sharing (`Private, Entity, Shared, Public`). A memory records
   its source (`ChatPromotion, RememberRequest, FileExtraction, ConversationExtraction` with the
   conversation and message ids) and a lifecycle `Unprocessed → Draft → Approved → Superseded |
   Invalidated | Archived`; **only Approved memories ground answers**. It also holds `disputes[]`
   (a Draft that contradicts an Approved memory is held in review), `lastReviewedAt` (refreshed by
   "Memory Reconciliation"), `supersededBy`, `usageCount`, `lastUsedAt` and `outcomes` (counters of
   what happened to the answers that used it). A **Memory Agent** derives glossary terms and metrics
   from memories (`derivedEntities` through `DERIVED_FROM` edges with `derivationEvidence`) as
   `provider: automation` proposals. They go through the approval workflow's automation branch with
   an `automationProposal {agent, confidence, derivedFrom}` payload that names sources by id only, so
   a task never leaks private memory text.
4. **Personas** have a `contextDefinition`: rules that materialise a shared AI context document per
   job function (`get_persona_context`, paged deterministically). Admin instructions are "never
   rendered into the markdown, which consumers treat as reference data rather than instructions".
   That is prompt-injection hygiene built into the format.
5. **MCP server** (`openmetadata-mcp`, `tools.json`). It has 21 tools: `search_metadata`,
   `semantic_search`, `find_context`, `get_concept_context`, `get_entity_details`,
   `get_entity_lineage`, `root_cause_analysis`, `get_test_definitions`, `create_test_case`,
   `create_lineage`, `create_entity`, `patch_entity` (RFC 6902), `describe_entity_type`,
   `get_user_context`, `get_persona_context`, `company_context`, `sparql_query`,
   `entity_neighborhood`, `find_by_tag`, `shacl_validate`, `ontology_describe`. The descriptions are
   worth copying as a style: they state what is *not* returned (`hasSql: true` instead of the SQL),
   they report partial views (`hiddenNodes` counts the lineage nodes removed for permission, and
   `hiddenNodesUnchecked` means the authorisation limit was hit), and they give pagination cursors.
6. **Semantic search** is hybrid: keyword plus vector, fused with RRF through an *ad-hoc* OpenSearch
   search pipeline. It is inlined per request, so each tenant keeps its own weights and no cluster
   admin privilege is needed (`OpenSearchVectorService`). Body-text contributors exist per type
   (query, test case, page, context memory).
7. **AI governance entities** (`entity/ai/`): `aiApplication`, `llmModel`, `promptTemplate`,
   `agentExecution`, `mcpServer`, `mcpExecution`, `aiGovernancePolicy`, `aiGovernanceFramework`
   (EU AI Act–style controls). Pluggable `llm/*CompletionClient` (Anthropic, Bedrock, Google, OpenAI)
   power descriptions, the Memory Agent and auto-classification.
8. **RDF/ontology**: an RDF projection with SPARQL, SHACL validation and ontology import/export
   (`ontologyConcept`, `ontologyChangeSet`, …). It is impressive but beside the point for us.

## 9. Governance workflows and approvals

**Engine.** Workflows are stored as JSON (`workflowDefinition {trigger, nodes[], edges[]}`) and
compiled to Flowable BPMN (`governance/workflows/flowable/MainWorkflow.java`). There are two trigger
types: `eventBasedEntity {entityTypes, events: Created|Updated, exclude: ["entityStatus"],
filter}` and `periodicBatchEntity {schedule, entityTypes, filters, batchSize}`. The nodes are
`startEvent`, `endEvent`, `parallelGateway`, `userTask.userApprovalTask {assignees {addReviewers,
addOwners, candidates, emptyAssigneeStrategy}, approvalThreshold, rejectionThreshold,
transitionMetadata[{id, label, targetStageId, requiresComment}]}`, and the automated tasks
`checkEntityAttributesTask` (JSONLogic, with custom operators such as `isReviewer`),
`setEntityAttributeTask`, `setGlossaryTermStatusTask`, `setEntityCertificationTask`,
`rollbackEntityTask`, `dataCompletenessTask`, `createAndRunIngestionPipelineTask`, `runAppTask`,
`sinkTask` (to git or a webhook), `createAndRunAIAutomationTask` and `policyAgentTask`. The edges
carry `condition` values (`true`/`false`, or a transition id). Nineteen workflows are seeded, among
them `GlossaryApprovalWorkflow`, `MetricApprovalWorkflow`, `AIAssetApprovalWorkflow`,
`DataQualityReviewTaskWorkflow`, `IncidentLifecycleWorkflow`, `DescriptionUpdateTaskWorkflow`,
`TagUpdateTaskWorkflow`, `OwnershipUpdateTaskWorkflow` and `RecognizerFeedbackReviewWorkflow`.

**The glossary (and metric) approval graph, verbatim in behaviour.** On create or update (changes
to `entityStatus` itself are ignored), the workflow first asks *was this proposed by an automation?*
If so, it uses the automation branch: approve directly if the updater is a reviewer, otherwise
In Review → approval task with `emptyAssigneeStrategy: assignAdmins` → Approved, Rejected, or (for
an update) **rollback**. Otherwise:
`CheckHasReviewers` — **false → set Approved** (no reviewers means auto-approved);
true → `CheckUpdatedByIsReviewer` — **true → Approved** (a reviewer's own edit auto-approves);
false → `CheckIsReadyToBeReviewed` (`!!description`) — false → Draft;
true → `CheckIsNew` (`version == 0.1`) — new: In Review → `ApproveGlossaryTerm` (threshold 1,
reject needs a comment) → Approved/Rejected; update: In Review → `ApprovalForUpdates` → Approved, or
**`rollbackEntityTask`** (reject reverts the entity to the last approved version).

**Change requests** (`governance/changeRequest/*`, new in 2026) make a proposed edit to a
*published* asset into a request with revisions. Each change is a `mutationOp {op: set|add|remove,
field, key, baseValue (the published value it was computed against), value, gated}`. The request
status is `Pending, Approved, Applied, Rejected, Conflicted, Withdrawn, Cancelled, Superseded`. It
detects `conflicts[{field, baseValue, currentValue, proposedValue}]`, `alreadyPublished` (another
change published the same value), `superseded` (the field was published with another value after
submission) and `disagreed` (the reviewers can no longer reach either threshold). Reviewer decisions
are an **append-only** `approvalDecision {revisionId, digest, decision: Approve|Reject|Override,
approvedChanges[], rejectedChanges[]}`, so one revision can be approved change by change
(`reviewPolicy.partialDecisions`). **Suggestions** (`SuggestDescription`, `SuggestTagLabel`, status
`Open/Accepted/Rejected`) are the lightweight AI path: an owner accepts or rejects a description an
LLM proposed.

**Access control.** A `Policy` has `rules[] {effect: allow|deny, resources[], operations[],
condition (SpEL over the functions `hasDomain(), isOwner(), noOwner(), matchAnyTag(…),
matchAllTags(…), inAnyTeam(…), hasAnyRole(…), isReviewer()`)}`. Roles
bundle policies; users and teams get roles; and *deny wins*. The operations are fine-grained:
`ViewAll, ViewBasic, ViewUsage, ViewTests, ViewQueries, ViewDataProfile, ViewSampleData,
ViewTestCaseFailedRowsSample, EditDescription, EditTags, EditGlossaryTerms, EditCertification,
EditLineage, EditTier, …` (60 operations). Seeded roles are `DataConsumer`,
`DataSteward` (edit descriptions, owners, tags, tier, glossary terms, certification and lineage),
and many bot roles (`IngestionBot`, `ProfilerBot`, `LineageBot`, `QualityBot`, `UsageBot`,
`AutoClassificationBot`). These are *metadata* permissions. OpenMetadata does not enforce row or
column security on the warehouse; it only *describes* it in contracts (`rowFilters`) and through
tags.

## 10. UI concepts worth noting

The page list (`openmetadata-ui/.../src/pages`) includes Explore (faceted search over everything),
an asset page with tabs (Schema, Activity Feed & Tasks, Sample Data, Queries, Profiler & Data
Quality, Lineage, Custom Properties, **Contract**), Glossary (a tree with term relations and an
approval banner), Domain and Data Product pages (Customizable*Page), DataMarketplacePage, MetricsPage,
IncidentManager, TestLibrary (the test definition catalogue), DataQuality, DataInsight and KPIPage
(description and ownership coverage targets over time), PlatformLineage (service-, domain- and
product-level lineage), ContextCenterPage, OntologyExplorer, SparqlPlayground, WorkflowDefinitions
(a visual workflow editor), EntityVersionPage (a version diff view driven by `changeDescription`),
and PendingChanges (change requests). Two patterns stand out. First, *one badge row per asset*:
tier, certification, contract status, DQ status, and "last updated by". Second, *version history as
a diff* of `fieldsAdded`/`fieldsUpdated`/`fieldsDeleted`.

---

## 11. Where we already stand (our side, located through `graphify query` and then read)

| Concern | Ours (path) | OpenMetadata |
| --- | --- | --- |
| Metric definition | `context.metric` + `context.metric_version` (`backend/libs/dataagent_db/models/context.py` L161, L201): versioned expression, filters, unit, `period_semantics`, `time_grain`, `time_dimension_id`, `allowed_dimensions`, `additivity`, `legal_basis`, status `draft→testing→pending_review→certified→superseded/broken`, `valid_from/to`; compiled by `services/worker_agent/semantic/compiler.py` | descriptive `metric` with an expression string; status through a generic workflow |
| Metric approval | `services/worker_agent/metric_changes.py` (preview: dependents, six-month side-by-side, filed figures; eval overlay) → `inbox.proposal` (`models/inbox.py` L29); two-person rule, invariant §5 | auto-approve when there are no reviewers or the updater is a reviewer; rollback on reject |
| Glossary | `context.glossary_term` (L417): term, `meanings` JSONB with a default meaning, `legal_basis`, `pending_review/active/retired`; `context.synonym`, `context.unmapped_phrase` | hierarchical terms, typed relations, synonyms, associated tags, **bindings to columns** |
| Relationships | `context.relationship` (L278): `origin declared/inferred/manual`, `overlap_ratio`, `pending_review` | FK constraints + **observed join counts** from query logs |
| Catalog | `catalog.dataset` (`models/catalog.py` L180), `dataset_version` with `schema_hash`, `diff`, `change_kind additive/breaking`, certification rows; `column_meta` with `pii_label`/`pii_confirmed`; `column_profile` per `scan_run` | table/column + time-series profiles + Major.Minor versions |
| Lineage | `pipeline.lineage_edge` (`models/pipeline.py` L136): `from_kind/to_kind` over source…data_product, `via`, `valid_to`; written by `pipelines.py`, `derived.py` | table + **column** lineage, `source` enum, SQL on the edge, from query logs, dbt, views, OpenLineage |
| Data products | `context.data_product` (L982: scope, sample questions ≥10, freshness statement, owner ≠ steward, classification, eval set, suspended_reason) + `pipeline.data_product` (L204: `contract` JSONB, consumers, 30-day breaking notice) | ODPS product + ports + SLA + inherited contract |
| DQ | `ingest.quality_rule` / `quality_result` (`models/ingest.py` L220/L258), `services/worker_agent/quality.py`: 7 kinds, severity high blocks reports (BR-116), acknowledgement with note | definitions library, dimensions, incidents, dynamic assertions, dimensional results |
| PII | `backend/libs/pii/__init__.py`: country packs, checksum-validated (CCCD, Luhn, IBAN); `column_label` | Presidio + spaCy NER, recognizers on tags, feedback → exception list |
| Trust | invariant 14 (VERIFIED, GOVERNED, EXPLORATORY, DOCUMENT, INSUFFICIENT); `sqlgen/verified.py`, `answer/verification.py`, `frontend/src/features/ask/dataAnswer.ts` | none at answer level; `stale`/`dataQuality` signals on context |
| Freshness | `backend/libs/shared/freshness.py` (data_as_of measured; owner, classification and lineage declared, never inferred) | contract SLA validation with `refreshedAtSource` |

Our design is DB-first through a generator (`docs/v3/07-database.md`: tab 3/3b →
`backend/schema/csdl/parse_d4.py` → `definitions.json` → DDL + ORM). Every schema proposal below has
to land in the design export first, then be generated. Do not hand-edit `models/*.py`.

---

## 12. What our product should take, mapped onto our modules

### 12.1 An "answer context envelope" with staleness and quality signals (feeds trust labels)

OpenMetadata's best idea for us is that **context travels with its own reliability**. Build
`services/worker_agent/agentic/context_envelope.py`: a pure function that assembles, for every
dataset and metric the agent touches, what `catalog.py` / `query.py` tools return today plus:
- `observability`: the latest `column_profile` (null ratio, distinct, min/max, top values that are
  already masked) and `row_estimate`, `profiled_at`;
- `quality`: from `ingest.quality_result`, `{total, passed, failed, open_incidents,
  blocking: bool}` (blocking = a high-severity rule failing without acknowledgement);
- `freshness`: `data_as_of` from `libs/shared/freshness.py`, compared with the data product's
  commitment;
- per knowledge item (glossary meaning, metric version, knowledge page): `stale` + `stale_reasons`
  (`dataset_changed_after_definition`: a `dataset_version.valid_from` later than the metric
  version's `certified_at`; `quality_failing`; `freshness_breached`; `certification_expired`).

The answer path then derives its **caveats deterministically** from this envelope (invariant 6: the
LLM narrates, it does not judge). GOVERNED stays GOVERNED only if no blocking signal exists. A failing
medium rule downgrades the label to "GOVERNED with caveat", and a high one blocks or forces
EXPLORATORY with the reason. Put the rules in `services/worker_agent/answer/verification.py` next
to the existing checks, so `number_check.verification` carries `{checks, verdict}` plus `signals[]`.
Two habits from the MCP descriptions are cheap to copy: say what was *withheld*
(`hidden_for_permission: n`, invariant 13), and mark counts as lower bounds when they were capped.

### 12.2 Glossary term → bindings, associated classification, typed relations

Extend `context.glossary_term` with a child table `context.glossary_binding(term_id, meaning_idx,
target_kind: column|dataset|metric|dimension|measure, target_id, status pending_review|active,
origin manual|inferred|agent_suggested, evidence JSONB)`. Today `meanings` JSONB carries target
mappings implicitly; making them rows gives us OM's `conceptContext`: for "khách hàng mua online"
the agent receives the bound metric `SO_KHACH_HANG_INTERNET`, the bound columns with their profiles,
and the approved example queries (`context.example_query`) as *evidence*, with `last_run_status`
from `query.query_run`. Add `associated_pii_label` on a term, so that binding a column to "Số CCCD"
sets `column_meta.pii_label` as a **derived** label that awaits confirmation. Add
`relation_type broader|narrower|synonym|related_to` + `provenance manual|inferred|imported|
ai_suggested` on term-to-term links. The resolver (`understand/resolver.py`, `agentic/grounded.py`)
already grounds quantity words in metric names and synonyms; bindings let grounding cover
*dimensions and filters* too ("khách hàng doanh nghiệp" → `customer_type = 'B'`).

### 12.3 Label provenance everywhere (TagLabel pattern)

Our `column_meta.pii_label` + `pii_confirmed`, and `description_status none|suggested|approved`,
are a narrow version of OM's TagLabel. Generalise this into one `label_origin` vocabulary
(`manual, automated, derived, propagated, generated`) + `state suggested|confirmed` + `reason` +
`applied_by` + `detector` metadata on PII labels, descriptions and dataset classification. Add a
**recognizer feedback** loop. When a steward rejects a PII suggestion, record it in
`catalog.pii_exception(pack, detector, column_id)`, so the next `schema_scans.py` run does not
re-propose it. The `libs/pii` docstring already argues that a guessed label is harmful; provenance is
how a suggestion stays visibly a suggestion.

### 12.4 DQ: incidents, dimensions, failed-row evidence

In `ingest.quality_rule` / `quality_result` and `services/worker_agent/quality.py`:
- add a **`quality_incident`** row (one open incident per rule, partial unique index, CLAUDE.md
  pattern): `status new|ack|assigned|resolved`, `severity`, `assignee`, `resolved_reason
  fixed|false_positive|auto_resolved|expired`, `failure_summary`. Auto-close when the next run
  passes, expire after a TTL, and auto-assign to the dataset steward (`catalog.dataset.steward_id`,
  then the Data Space steward). Our "acknowledged_by + note" becomes the `ack` transition;
- add `dimension` to `quality_rule` (`completeness, validity, uniqueness, timeliness, accuracy,
  consistency`), so the envelope can say *"this table is incomplete"* rather than *"rule 7 failed"*;
- add `failed_ratio` and `inspection_sql` (the guarded query that lists violating rows, already
  masked through `sample_ref`); the agent can then offer "show me the failing rows" as a governed
  follow-up;
- add **dimensional runs** (`group_by` param) ranked by OM's impact score, prose-reimplemented. This
  directly answers "why is the number for region X odd".

The incidents surface in the inbox (`features/needs-you/`) and in the answer envelope
(`open_incidents`).

### 12.5 Data contract as a first-class, validated object

We have two partial contracts: `pipeline.data_product.contract` JSONB ("Cột, kiểu, ý nghĩa, SLA")
and `context.data_product.freshness_statement` + primary-member freshness. Adopt OM's sectioned
contract as **one** table, `pipeline.data_contract(subject_kind dataset|data_product, subject_id,
version, schema JSONB, sla {refresh_every, max_latency, available_by, timezone, freshness_column},
quality_rule_ids[], security {classification, policy_ids[]}, terms_of_use_for_agents, status
draft|active|deprecated, effective_from/until)`, plus a time series
`pipeline.contract_result(status running|success|failed|partial|aborted, schema_check, sla_check
{met, actual_latency, last_refreshed_at, refreshed_at_source}, quality_check {passed, failed,
score}, incident_id)`. Validation reuses what exists: the schema check is `schema_scans.py`'s diff
against the contract (`dataset_version.change_kind = breaking` → contract failed); the SLA check is
`freshness.py`; the quality check is `quality.py`. The data product's suspension (`suspended_reason
metric_decertified|dataset_decertified`) gains `contract_failed`. The terms of use become an
agent-facing **usage note** in the envelope (for example "không dùng cho báo cáo nộp NHNN trước
16:30"). The ODCS export can come later; keep the field names ODCS-compatible.

### 12.6 Query-log mining: usage, frequent joins, column lineage

OM's highest-yield pipeline for SQL generation is query history → joins and usage. We already have
sqlglot (`backend/libs/sql_guard`). Add a worker step, `services/worker_agent/query_history.py`,
scheduled per source, which:
1. reads the source's history (Postgres `pg_stat_statements`; Snowflake `QUERY_HISTORY`; BigQuery
   `INFORMATION_SCHEMA.JOBS`), excluding our own tagged queries (prefix every executor query with a
   `/* dataagent:<run_id> */` comment, as OM does);
2. masks literals before storing anything (invariant on PII; OM's `masker.py` idea);
3. parses with sqlglot into tables, column pairs and join predicates;
4. writes `catalog.dataset_usage(day, count, distinct_users)` and `catalog.join_observation(from
   column, to column, join_count, window)`;
5. **proposes** `context.relationship` rows with `origin = inferred`, evidence `join_count` next to
   `overlap_ratio`, status `pending_review` (invariant 5);
6. writes `pipeline.lineage_edge` with `via = query` and a new `columns` JSONB (`[{from[], to,
   function}]`) and `sql_hash`.

Our *own* query runs (`query.query_run`) are a second, perfectly attributed log. Mining them gives
"which tables answered which metric" for free.

### 12.7 Column lineage + root-cause tool for explaining numbers

With column-level edges, a `trace_metric` agent tool (`agentic/tools/`) can answer "số này lấy từ
đâu": metric version → measure → column → upstream column(s) → source, plus any failing rule or open
incident upstream (OM's `root_cause_analysis`: upstream failures first, then downstream impact).
This turns the answer drawer's "open the SQL" (invariant 14) into "open the chain", and it feeds the
explanation of a change ("doanh thu tháng 9 giảm vì bảng X chưa nạp ngày 30": `freshness_breached`
upstream). Render it in `frontend/src/features/data-prep/LineagePage.tsx`, with a per-answer entry
point.

### 12.8 Per-field change requests with base values (OCC on proposals)

Our invariant 10 (no last-write-wins) is enforced per row. OM's `mutationOp {op, field, key,
baseValue, value}` + `conflicts[]` + `superseded` + append-only `approvalDecision` with per-change
approve/reject is the finer-grained version. Adopt it in `inbox.proposal.diff` for glossary,
synonym and dataset-description proposals (not metric versions, which are whole versions already).
Store `base_value` per field, mark a proposal `conflicted` when the published value moved, and let a
reviewer approve some changes and reject others. Keep our two-person rule. Do **not** copy OM's
"no reviewers → auto-approve" or "updater is a reviewer → auto-approve".

### 12.9 Agent learnings as reviewed memories

Liked answers, verified queries and the Error Book already exist (`liked.py`, `sqlgen/verified.py`,
eval). OM's `contextMemory` adds three things worth having: a **lifecycle with reconciliation**
(Draft → Approved → Superseded | Invalidated, `last_reviewed_at`, re-validated when the underlying
dataset or metric version changes); **disputes** (a new learning that contradicts an approved one
waits for review instead of overwriting it); and **outcome counters** (how many answers that used
this memory were later liked, corrected or flagged). Map this onto `context.example_query` (status
plus `last_validated_at` plus `used_count` plus `outcomes`) and the knowledge pages, rather than
creating a new store.

### 12.10 Certification with expiry, tiers, personas, and the tool contract

- Add `certified_until` to `metric_version` and to the `dataset_version` certification, and an
  `expiry` signal in the envelope (a metric certified 18 months ago should be re-reviewed). Add a
  **tier** (criticality 1–5) on datasets and metrics to drive the order of DQ coverage, review SLAs
  and alert severity.
- Add a persona-scoped context, for example "Kế toán trưởng" or "Phân tích bán hàng": a curated set
  of data products and metrics placed first in retrieval, with admin instructions kept *outside* the
  reference text (OM's injection hygiene).
- Write MCP and agent tool descriptions the OM way: say what is omitted (`has_sql`), report partial
  results (`hidden`, `truncated`, lower bounds), and use cursors. Apply this to
  `services/worker_agent/tools/mcp.py` and `agentic/tools/*`.

### How glossary + lineage + contracts + DQ feed the analyst agent (the end-to-end picture)

1. **Grounding (before generation).** The question words resolve through glossary meanings and
   **bindings** to metric versions, dimensions and filter values (12.2). Unbound words are
   `unmapped_phrase` entries, as today. Bindings carry evidence (approved example queries) that the
   SQL writer may copy for EXPLORATORY answers, and observed **frequent joins** (12.6) rank the join
   paths the sqlgen vote considers.
2. **Eligibility (before execution).** The data product's active **contract** says whether the
   subject is in SLA and whether its quality expectations pass (12.5). A breached contract does not
   block exploration, but it removes GOVERNED eligibility and states why (invariant 14).
3. **Qualification (with the result).** The envelope signals (12.1), freshness, quality,
   staleness, open incidents and expired certification, become deterministic caveats and label
   adjustments in `answer/verification.py`, shown in `dataAnswer.ts` next to the label.
4. **Explanation (after the answer).** Column lineage + root cause (12.7) explain *where the number
   came from* and *what upstream might be wrong*. The metric version's `change_note` and
   `dataset_version.diff` explain *why it differs from last month's report*.
5. **Learning (after feedback).** Corrections become memory or example drafts with disputes and
   outcomes (12.9). They never edit production context without review (invariant 12).

---

## 13. Ranked: the ten most valuable things to adopt

| # | Adopt | Why it matters for us | Where | Effort |
| --- | --- | --- | --- | --- |
| 1 | **Answer context envelope with `stale`/`stale_reasons`, observability and DQ standing**, turned into deterministic caveats and label downgrades | makes invariant 14 honest: a GOVERNED number on a broken table stops looking trustworthy | `agentic/` (new `context_envelope.py`), `answer/verification.py`, `features/ask/dataAnswer.ts` | M |
| 2 | **DQ incident lifecycle** (one open per rule, new→ack→assigned→resolved, auto-close on pass, TTL, auto-assign steward) + **dimensions** + failed ratio + inspection SQL | turns `quality_result` rows into owned work, and gives the envelope an `open_incidents` count | `models/ingest.py` (via design), `worker_agent/quality.py`, inbox | M |
| 3 | **Glossary bindings** (term meaning → column/metric/dimension/value, reviewed) + associated PII label + typed term relations | grounding for filters and dimensions, and concept context with evidence | `context.glossary_term` + new `glossary_binding`, `understand/resolver.py`, `routes_semantic.py` | M |
| 4 | **Query-log mining with sqlglot** → usage, frequent joins (as `relationship` proposals with join counts), table lineage | the strongest SQL-generation signal OM has; reuses `libs/sql_guard` | new `worker_agent/query_history.py`, `catalog`, `context.relationship` | L |
| 5 | **First-class data contract + validation result series** (schema / SLA / quality / terms of use for agents) | unifies our two partial contracts; gives a product suspension reason and a GOVERNED eligibility gate | `pipeline` schema, `freshness.py`, `schema_scans.py`, `quality.py`, `routes_products.py` | M–L |
| 6 | **Label provenance** (`origin`, `state suggested/confirmed`, `reason`, `applied_by`, detector) + **recognizer feedback exceptions** | stops re-proposing rejected PII labels; every label shows who or what set it | `catalog.column_meta`, `libs/pii`, `schema_scans.py` | S–M |
| 7 | **Column-level lineage on `lineage_edge` + a `trace_metric` / root-cause tool** | "where does this number come from / what upstream is broken" in the answer drawer | `pipeline.lineage_edge.columns`, `agentic/tools/`, `LineagePage.tsx` | M |
| 8 | **Per-field mutation ops with `base_value`, conflict states, per-change decisions** on proposals | finer-grained invariant 10 for glossary, description and synonym edits | `inbox.proposal.diff`, `semantic_review.py`, `ApprovalChainPage.tsx` | M |
| 9 | **Reviewed agent memories**: reconciliation, disputes, outcome counters on examples and learnings | safer self-improvement (invariant 12) with evidence of usefulness | `context.example_query`, `liked.py`, `sqlgen/verified.py` | M |
| 10 | **Certification expiry + tier + persona context + OM-style tool contracts** (`hidden`, `truncated`, `has_sql`) | cheap hygiene that improves review cadence and agent behaviour | `metric_version`, `dataset_version`, `tools/mcp.py`, `agentic/tools/*` | S |

## 14. What to skip

- **Flowable/BPMN governance-workflow engine** and the visual workflow editor. Our inbox proposals
  with fixed per-kind review paths are enough. A JSON trigger→nodes→edges graph is only worth it if
  customers ask for configurable approval chains, and even then a small in-house state table beats
  Flowable.
- **Auto-approval rules** ("no reviewers → Approved", "updater is a reviewer → Approved"). They
  contradict our two-person rule (`allow_self_approval` off by default).
- **Major.Minor metadata versions with 10-minute session consolidation.** Our explicit
  definition versions (`metric_version`, `dataset_version`) are the right unit, and consolidation
  rewrites history, which conflicts with audit.
- **Last-write-wins PATCH with optional `If-Match`.** We already require OCC.
- **RDF/SPARQL/SHACL/ontology modules**, Data Insights web analytics, KPI pages, votes, followers,
  announcements and activity-feed threads. They are big surfaces with little value for answering
  questions.
- **The 97-connector ingestion framework and its code.** It is under the Collate licence, and our
  in-place query model (invariant 15) needs only connection, schema scan, profile and query history
  per dialect.
- **Presidio + spaCy NER for PII.** It is heavy and English- and European-centric; our checksum-
  validated country packs are more precise for VN identifiers. Keep only the *feedback* idea.
- **Contract `semantics` as JSONLogic over metadata** ("has an owner"). It is low value; our
  data-product activation checks already enforce owner ≠ steward and similar rules.
- **OM's `metric` entity shape as our model.** Ours is richer. Only borrow `metricType` and
  `granularity` naming for the YAML export (`semantic_yaml.py`) if interoperability is needed.
- **Elasticsearch/OpenSearch** as a new dependency. Use Postgres FTS plus pgvector with RRF fusion
  (the idea is fine, the cluster is not needed).

---

## Appendix: verbatim key schemas

The JSON below is re-serialised from the schema files with indent 1. Keys and values are unchanged;
only the omissions named on each block were made.

#### glossaryTerm.json (properties, required)

Source: `openmetadata-spec/src/main/resources/json/schema/entity/data/glossaryTerm.json`. Common envelope fields omitted for length: `style, updatedAt, updatedBy, impersonatedBy, href, usageCount, incrementalChangeDescription, deleted, provider, extension, votes, childrenCount` (same meaning on every entity)

```json
{
 "definitions": {
  "termReference": {
   "type": "object",
   "properties": {
    "name": {
     "description": "Name that identifies the source of an external glossary term. Example `HealthCare.gov`.",
     "type": "string"
    },
    "endpoint": {
     "description": "Name that identifies the source of an external glossary term. Example `HealthCare.gov`.",
     "type": "string",
     "format": "uri"
    }
   },
   "additionalProperties": false
  }
 },
 "properties": {
  "id": {
   "description": "Unique identifier of a glossary term instance.",
   "$ref": "../../type/basic.json#/definitions/uuid"
  },
  "name": {
   "description": "Preferred name for the glossary term.",
   "$ref": "../../type/basic.json#/definitions/entityName"
  },
  "displayName": {
   "description": "Display Name that identifies this glossary.",
   "type": "string"
  },
  "description": {
   "description": "Description of the glossary term.",
   "$ref": "../../type/basic.json#/definitions/markdown"
  },
  "fullyQualifiedName": {
   "description": "A unique name that identifies a glossary term. It captures name hierarchy of glossary of terms in the form of `glossaryName.parentTerm.childTerm`.",
   "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "iri": {
   "description": "Canonical IRI of this term in its source ontology (e.g., `http://example.com/ontology/hcp#HealthcareProvider`). Preserves identity across ontology import/export round-trips and is used as the RDF subject when present. Empty for terms not sourced from an external ontology.",
   "type": "string",
   "format": "uri"
  },
  "synonyms": {
   "description": "Alternate names that are synonyms or near-synonyms for the glossary term.",
   "type": "array",
   "items": {
    "$ref": "../../type/basic.json#/definitions/entityName"
   }
  },
  "glossary": {
   "description": "Glossary that this term belongs to.",
   "$ref": "../../type/entityReference.json"
  },
  "parent": {
   "description": "Parent glossary term that this term is child of. When `null` this term is the root term of the glossary.",
   "$ref": "../../type/entityReference.json"
  },
  "children": {
   "description": "Other glossary terms that are children of this glossary term.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "relatedTerms": {
   "description": "Other glossary terms that are related to this glossary term with typed semantic relations.",
   "type": "array",
   "items": {
    "$ref": "../../type/termRelation.json"
   },
   "default": []
  },
  "references": {
   "description": "Link to a reference from an external glossary.",
   "type": "array",
   "items": {
    "$ref": "../../entity/data/glossaryTerm.json#/definitions/termReference"
   }
  },
  "conceptMappings": {
   "description": "Optional mappings to external concepts (e.g., SKOS alignments).",
   "type": "array",
   "items": {
    "$ref": "../../type/conceptMapping.json"
   },
   "default": []
  },
  "version": {
   "description": "Metadata version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/entityVersion"
  },
  "reviewers": {
   "description": "User names of the reviewers for this glossary.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "owners": {
   "description": "Owners of this glossary term.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "tags": {
   "description": "Tags associated with this glossary term. These tags captures relationship of a glossary term with a tag automatically. As an example a glossary term 'User.PhoneNumber' might have an associated tag 'PII.Sensitive'. When 'User.Address' is used to label a column in a table, 'PII.Sensitive' label is also applied automatically due to Associated tag relationship.",
   "type": "array",
   "items": {
    "$ref": "../../type/tagLabel.json"
   },
   "default": []
  },
  "changeDescription": {
   "description": "Change that lead to this version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/changeDescription"
  },
  "entityStatus": {
   "description": "Approval status of the glossary term.",
   "$ref": "../../type/status.json"
  },
  "disabled": {
   "description": "System glossary can't be deleted. Use this flag to disable them.",
   "type": "boolean"
  },
  "mutuallyExclusive": {
   "description": "Glossary terms that are children of this term are mutually exclusive. When mutually exclusive is `true` only one term can be used to label an entity from this group. When mutually exclusive is `false`, multiple terms from this group can be used to label an entity.",
   "type": "boolean",
   "default": "false"
  },
  "domains": {
   "description": "Domains the Glossary Term belongs to. When not set, the Glossary TErm inherits the domain from the Glossary it belongs to.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "dataProducts": {
   "description": "List of data products this entity is part of.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "dataContract": {
   "description": "Reference to the data contract for this entity.",
   "$ref": "../../type/entityReference.json"
  }
 },
 "required": [
  "id",
  "name",
  "description",
  "glossary"
 ]
}
```

#### type/termRelation.json (typed term relation)

Source: `openmetadata-spec/src/main/resources/json/schema/type/termRelation.json`

```json
{
 "id": {
  "description": "Unique identifier of this relation edge.",
  "$ref": "basic.json#/definitions/uuid"
 },
 "relationType": {
  "description": "Type of the relation (e.g., 'broader', 'narrower', 'synonym', 'relatedTo'). Defaults to 'relatedTo' for backward compatibility.",
  "$ref": "#/definitions/relationType"
 },
 "relationshipType": {
  "description": "Resolved first-class relationship type.",
  "$ref": "entityReference.json"
 },
 "term": {
  "description": "Reference to the related glossary term.",
  "$ref": "entityReference.json"
 },
 "provenance": {
  "description": "How this relation edge originated. Defaults to 'Manual'.",
  "$ref": "#/definitions/provenance"
 },
 "status": {
  "description": "Approval status of this relation edge.",
  "$ref": "status.json"
 },
 "createdBy": {
  "description": "User who first authored or imported the relationship.",
  "type": "string"
 },
 "createdAt": {
  "description": "Time the relationship was first persisted.",
  "$ref": "basic.json#/definitions/timestamp"
 },
 "_provenance_enum": [
  "Manual",
  "Inferred",
  "Imported",
  "AiSuggested"
 ]
}
```

#### metric.json (definitions, properties)

Source: `openmetadata-spec/src/main/resources/json/schema/entity/data/metric.json`. Common envelope fields omitted for length: `childrenCount, updatedAt, updatedBy, createdAt, createdBy, impersonatedBy, href, followers, incrementalChangeDescription, deleted, votes, extension, provider` (same meaning on every entity). `javaEnums` duplicates removed

```json
{
 "definitions": {
  "metricExpression": {
   "type": "object",
   "properties": {
    "language": {
     "javaType": "org.openmetadata.schema.type.MetricExpressionLanguage",
     "description": "This schema defines the type of the language used for Metric Expression Code.",
     "type": "string",
     "enum": [
      "SQL",
      "Java",
      "JavaScript",
      "Python",
      "External"
     ]
    },
    "code": {
     "javaType": "org.openmetadata.schema.type.MetricExpressionCode",
     "description": "This schema defines the type of the language used for Metric Formula's Code.",
     "type": "string"
    }
   },
   "additionalProperties": false
  },
  "metricType": {
   "javaType": "org.openmetadata.schema.type.MetricType",
   "description": "This schema defines the type of Metric.",
   "type": "string",
   "enum": [
    "COUNT",
    "SUM",
    "AVERAGE",
    "RATIO",
    "PERCENTAGE",
    "MIN",
    "MAX",
    "MEDIAN",
    "MODE",
    "STANDARD_DEVIATION",
    "VARIANCE",
    "SIMPLE",
    "CUMULATIVE",
    "DERIVED",
    "CONVERSION",
    "OTHER"
   ]
  },
  "unitOfMeasurement": {
   "javaType": "org.openmetadata.schema.type.MetricUnitOfMeasurement",
   "description": "This schema defines the type of Metric's unit of measurement.",
   "type": "string",
   "enum": [
    "COUNT",
    "DOLLARS",
    "PERCENTAGE",
    "TIMESTAMP",
    "SIZE",
    "REQUESTS",
    "EVENTS",
    "TRANSACTIONS",
    "OTHER"
   ]
  },
  "metricGranularity": {
   "javaType": "org.openmetadata.schema.type.MetricGranularity",
   "description": "This schema defines the type of Metric's granularity.",
   "type": "string",
   "enum": [
    "SECOND",
    "MINUTE",
    "HOUR",
    "DAY",
    "WEEK",
    "MONTH",
    "QUARTER",
    "YEAR"
   ]
  },
  "metricDimension": {
   "type": "object",
   "description": "A dimension associated with a metric from a semantic layer.",
   "properties": {
    "name": {
     "type": "string",
     "description": "Name of the dimension."
    },
    "fullyQualifiedName": {
     "description": "Fully qualified name of the dimension, in the format 'metricFQN.dimension.<name>'.",
     "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
    },
    "type": {
     "type": "string",
     "description": "Type of the dimension.",
     "enum": [
      "CATEGORICAL",
      "TIME"
     ]
    },
    "description": {
     "type": "string",
     "description": "Description of the dimension."
    },
    "expression": {
     "type": "string",
     "description": "Expression used to compute the dimension."
    }
   },
   "required": [
    "name"
   ],
   "additionalProperties": false
  },
  "metricMeasure": {
   "type": "object",
   "description": "A measure associated with a metric from a semantic layer.",
   "properties": {
    "name": {
     "type": "string",
     "description": "Name of the measure."
    },
    "fullyQualifiedName": {
     "description": "Fully qualified name of the measure, in the format 'metricFQN.measure.<name>'.",
     "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
    },
    "aggregation": {
     "type": "string",
     "description": "Aggregation function for the measure (e.g., sum, count, average)."
    },
    "description": {
     "type": "string",
     "description": "Description of the measure."
    },
    "expression": {
     "type": "string",
     "description": "Expression used to compute the measure."
    }
   },
   "required": [
    "name"
   ],
   "additionalProperties": false
  },
  "metricFilter": {
   "type": "object",
   "description": "A filter applied to a metric.",
   "properties": {
    "where": {
     "type": "string",
     "description": "SQL WHERE clause for the filter."
    }
   },
   "required": [
    "where"
   ],
   "additionalProperties": false
  }
 },
 "properties": {
  "id": {
   "description": "Unique identifier that identifies this Metric instance.",
   "$ref": "../../type/basic.json#/definitions/uuid"
  },
  "name": {
   "description": "Name that identifies this Metric instance uniquely.",
   "$ref": "../../type/basic.json#/definitions/entityName"
  },
  "fullyQualifiedName": {
   "description": "A unique name that identifies a metric in the format 'ServiceName.MetricName'.",
   "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "displayName": {
   "description": "Display Name that identifies this metric.",
   "type": "string"
  },
  "description": {
   "description": "Description of metrics instance, what it is, and how to use it.",
   "$ref": "../../type/basic.json#/definitions/markdown"
  },
  "metricExpression": {
   "description": "Expression used to compute the metric.",
   "$ref": "#/definitions/metricExpression"
  },
  "metricType": {
   "description": "Type of the metric.",
   "$ref": "#/definitions/metricType"
  },
  "unitOfMeasurement": {
   "description": "Unit of measurement for the metric.",
   "$ref": "#/definitions/unitOfMeasurement"
  },
  "customUnitOfMeasurement": {
   "description": "Custom unit of measurement when unitOfMeasurement is OTHER.",
   "type": "string"
  },
  "granularity": {
   "description": "Metric's granularity.",
   "$ref": "#/definitions/metricGranularity"
  },
  "dimensions": {
   "description": "Dimensions associated with this metric from a semantic layer.",
   "type": "array",
   "items": {
    "$ref": "#/definitions/metricDimension"
   },
   "default": null
  },
  "measures": {
   "description": "Measures associated with this metric from a semantic layer.",
   "type": "array",
   "items": {
    "$ref": "#/definitions/metricMeasure"
   },
   "default": null
  },
  "filters": {
   "description": "Filters applied to this metric.",
   "type": "array",
   "items": {
    "$ref": "#/definitions/metricFilter"
   },
   "default": null
  },
  "relatedMetrics": {
   "description": "Related Metrics.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "parent": {
   "description": "Parent metric this metric is a variant of. Metric fully qualified names stay flat, so the hierarchy is tracked purely through CONTAINS relationships and reparenting never rewrites the fully qualified name.",
   "$ref": "../../type/entityReference.json"
  },
  "metricGroup": {
   "description": "Metric Group this metric belongs to, if any. A group organizes metrics for browsing; membership is a relationship, so it is derived on read and never stored on the metric.",
   "$ref": "../../type/entityReference.json"
  },
  "children": {
   "description": "Immediate child metrics (variants) of this metric.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "assets": {
   "description": "Data assets (tables, columns, dashboards, etc.) this metric is computed on or applies to. Establishes a first-class metric-to-asset relationship so the metric can be surfaced as context for those assets.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "version": {
   "description": "Metadata version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/entityVersion"
  },
  "owners": {
   "description": "Owners of this metrics.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "experts": {
   "description": "Users who are experts in this Metric.",
   "$ref": "../../type/entityReferenceList.json",
   "default": null
  },
  "reviewers": {
   "description": "Reviewers of this Metric.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "tags": {
   "description": "Tags for this chart.",
   "type": "array",
   "items": {
    "$ref": "../../type/tagLabel.json"
   },
   "default": []
  },
  "changeDescription": {
   "description": "Change that lead to this version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/changeDescription"
  },
  "domains": {
   "description": "Domains the Glossary belongs to.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "dataProducts": {
   "description": "List of data products this entity is part of.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "dataContract": {
   "description": "Reference to the data contract for this entity.",
   "$ref": "../../type/entityReference.json"
  },
  "certification": {
   "$ref": "../../type/assetCertification.json"
  },
  "entityStatus": {
   "description": "Status of the Metric.",
   "$ref": "../../type/status.json"
  },
  "derivedFrom": {
   "description": "Derived: the context memory from which the Memory Agent created this metric (DERIVED_FROM edge, read-only projection).",
   "$ref": "../../type/entityReference.json"
  }
 },
 "required": [
  "id",
  "name"
 ]
}
```

#### dataProduct.json (definitions, properties)

Source: `openmetadata-spec/src/main/resources/json/schema/entity/domains/dataProduct.json`. Common envelope fields omitted for length: `style, updatedAt, updatedBy, impersonatedBy, href, incrementalChangeDescription, extension, followers, votes` (same meaning on every entity)

```json
{
 "definitions": {
  "lifecycleStage": {
   "description": "Lifecycle stage of the data product",
   "type": "string",
   "enum": [
    "IDEATION",
    "DESIGN",
    "DEVELOPMENT",
    "TESTING",
    "PRODUCTION",
    "DEPRECATED",
    "RETIRED"
   ],
   "default": "DEVELOPMENT"
  },
  "dataProductType": {
   "description": "Type of the data product, aligned with the Open Data Product Standard (ODPS) v4.1 type taxonomy.",
   "type": "string",
   "enum": [
    "RAW_DATA",
    "DERIVED_DATA",
    "DATASET",
    "REPORTS",
    "ANALYTIC_VIEW",
    "VISUALISATION_3D",
    "ALGORITHM",
    "DECISION_SUPPORT",
    "AUTOMATED_DECISION_MAKING",
    "DATA_ENHANCED_PRODUCT",
    "DATA_DRIVEN_SERVICE",
    "DATA_ENABLED_PERFORMANCE",
    "BI_DIRECTIONAL"
   ]
  },
  "visibility": {
   "description": "Visibility level of the data product. Controls who can discover and access this data product. Promotion to more open levels (e.g., ORGANISATION, PUBLIC) should go through the governance workflow.",
   "type": "string",
   "enum": [
    "PRIVATE",
    "INVITATION",
    "ORGANISATION",
    "DATASPACE",
    "PUBLIC"
   ],
   "default": "PRIVATE"
  },
  "portfolioPriority": {
   "description": "Portfolio-level priority of this data product, indicating its importance in the organization's data product portfolio.",
   "type": "string",
   "enum": [
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW"
   ]
  },
  "portType": {
   "description": "Type of the data product port",
   "type": "string",
   "enum": [
    "INPUT",
    "OUTPUT"
   ]
  },
  "portProtocol": {
   "description": "Protocol used by the port for data access",
   "type": "string",
   "enum": [
    "REST",
    "GRPC",
    "GRAPHQL",
    "JDBC",
    "KAFKA",
    "FILE",
    "S3",
    "AZURE_BLOB",
    "GCS",
    "WEBHOOK",
    "CUSTOM"
   ],
   "default": "REST"
  },
  "portFormat": {
   "description": "Data format supported by the port",
   "type": "string",
   "enum": [
    "JSON",
    "CSV",
    "PARQUET",
    "AVRO",
    "ORC",
    "XML",
    "PROTOBUF",
    "DELTA",
    "ICEBERG",
    "CUSTOM"
   ],
   "default": "JSON"
  },
  "dataProductPort": {
   "description": "Port definition for data product input/output",
   "type": "object",
   "properties": {
    "name": {
     "description": "Name of the port",
     "type": "string"
    },
    "displayName": {
     "description": "Display name of the port",
     "type": "string"
    },
    "description": {
     "description": "Description of the port",
     "type": "string"
    },
    "portType": {
     "$ref": "#/definitions/portType"
    },
    "protocol": {
     "$ref": "#/definitions/portProtocol"
    },
    "format": {
     "$ref": "#/definitions/portFormat"
    },
    "endpoint": {
     "description": "Endpoint URL or connection string",
     "type": "string"
    },
    "dataAsset": {
     "description": "Reference to the data asset exposed through this port",
     "$ref": "../../type/entityReference.json"
    }
   },
   "required": [
    "name",
    "portType",
    "dataAsset"
   ],
   "additionalProperties": false
  },
  "slaDefinition": {
   "description": "Service Level Agreement definition",
   "type": "object",
   "properties": {
    "tier": {
     "description": "SLA tier (e.g., GOLD, SILVER, BRONZE)",
     "type": "string",
     "enum": [
      "GOLD",
      "SILVER",
      "BRONZE",
      "CUSTOM"
     ]
    },
    "availability": {
     "description": "Expected availability percentage (e.g., 99.9)",
     "type": "number",
     "minimum": 0,
     "maximum": 100
    },
    "responseTime": {
     "description": "Expected response time in milliseconds",
     "type": "integer"
    },
    "dataFreshness": {
     "description": "Maximum data staleness in minutes",
     "type": "integer"
    },
    "dataQuality": {
     "description": "Minimum data quality score",
     "type": "number",
     "minimum": 0,
     "maximum": 100
    }
   },
   "additionalProperties": false
  }
 },
 "properties": {
  "id": {
   "description": "Unique ID of the Data Product",
   "$ref": "../../type/basic.json#/definitions/uuid"
  },
  "name": {
   "description": "A unique name of the Data Product",
   "$ref": "../../type/basic.json#/definitions/entityName"
  },
  "fullyQualifiedName": {
   "description": "FullyQualifiedName is `domain.dataProductName` or `sub-domain.dataProductName`.",
   "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "displayName": {
   "description": "Name used for display purposes. Example 'Marketing', 'Payments', etc.",
   "type": "string"
  },
  "description": {
   "description": "Description of the Data Product.",
   "$ref": "../../type/basic.json#/definitions/markdown"
  },
  "version": {
   "description": "Metadata version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/entityVersion"
  },
  "owners": {
   "description": "Owners of this Data Product.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "experts": {
   "description": "List of users who are experts for this Data Product.",
   "$ref": "../../type/entityReferenceList.json",
   "default": null
  },
  "reviewers": {
   "description": "User references of the reviewers for this Data Product.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "domains": {
   "description": "Domains or sub-domains to which this Data Product belongs to.",
   "$ref": "../../type/entityReferenceList.json"
  },
  "assets": {
   "description": "Data assets collection that is part of this data product.",
   "$ref": "../../type/entityReferenceList.json",
   "deprecated": true,
   "$comment": "@deprecated Use GET /v1/dataProducts/{id}/assets API endpoint for paginated access to data product assets"
  },
  "lifecycleStage": {
   "description": "Current lifecycle stage of the data product. Driven by the governance workflow — not directly user-editable.",
   "$ref": "#/definitions/lifecycleStage"
  },
  "dataProductType": {
   "description": "Type of the data product (ODPS-aligned taxonomy).",
   "$ref": "#/definitions/dataProductType"
  },
  "visibility": {
   "description": "Visibility level of the data product in the marketplace.",
   "$ref": "#/definitions/visibility"
  },
  "portfolioPriority": {
   "description": "Portfolio-level priority of this data product.",
   "$ref": "#/definitions/portfolioPriority"
  },
  "sla": {
   "description": "Service Level Agreement for this data product",
   "$ref": "#/definitions/slaDefinition"
  },
  "consumesFrom": {
   "description": "Other data products that this product consumes data from",
   "$ref": "../../type/entityReferenceList.json"
  },
  "providesTo": {
   "description": "Other data products that consume data from this product",
   "$ref": "../../type/entityReferenceList.json"
  },
  "tags": {
   "description": "Tags associated with the Data Product.",
   "type": "array",
   "items": {
    "$ref": "../../type/tagLabel.json"
   },
   "default": []
  },
  "changeDescription": {
   "description": "Change that lead to this version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/changeDescription"
  },
  "certification": {
   "$ref": "../../type/assetCertification.json"
  },
  "entityStatus": {
   "description": "Status of the Data Product.",
   "$ref": "../../type/status.json"
  }
 },
 "required": [
  "id",
  "name",
  "description"
 ]
}
```

#### dataContract.json (definitions, properties)

Source: `openmetadata-spec/src/main/resources/json/schema/entity/data/dataContract.json`. Common envelope fields omitted for length: `updatedAt, updatedBy, impersonatedBy, createdAt, createdBy, href, incrementalChangeDescription, deleted, extension` (same meaning on every entity). The 36-value timezone enum is shortened

```json
{
 "definitions": {
  "contractStatus": {
   "javaType": "org.openmetadata.schema.type.ContractStatus",
   "description": "Status of the data contract.",
   "type": "string",
   "enum": [
    "Draft",
    "Active",
    "Deprecated"
   ]
  },
  "contractUpdate": {
   "type": "object",
   "javaType": "org.openmetadata.schema.type.ContractUpdate",
   "description": "Record of updates to the data contract.",
   "properties": {
    "timestamp": {
     "description": "Timestamp when the contract was updated.",
     "$ref": "../../type/basic.json#/definitions/timestamp"
    },
    "updatedBy": {
     "description": "User who updated the contract.",
     "type": "string"
    },
    "impersonatedBy": {
     "description": "Bot user that performed the action on behalf of the actual user.",
     "$ref": "../../type/basic.json#/definitions/impersonatedBy"
    },
    "changeDescription": {
     "description": "Description of changes made to the contract.",
     "$ref": "../../type/basic.json#/definitions/markdown"
    },
    "version": {
     "description": "Version number of the contract after the update.",
     "type": "string"
    }
   },
   "required": [
    "timestamp",
    "updatedBy",
    "version"
   ],
   "additionalProperties": false
  },
  "rowFilter": {
   "type": "object",
   "description": "Filter that defines what subset of the data is accessible to certain consumers",
   "properties": {
    "columnName": {
     "type": "string",
     "title": "Column Name",
     "description": "Column to apply the filter"
    },
    "values": {
     "type": "array",
     "title": "Filter Values",
     "description": "Values applied to the filter",
     "items": {
      "type": "string"
     }
    }
   }
  },
  "policy": {
   "type": "object",
   "description": "Intended consumers of the data (e.g. internal teams, external partners, etc.)",
   "properties": {
    "accessPolicy": {
     "type": "string",
     "title": "Access Policy",
     "description": "Reference to an access policy ID or name that should govern this data"
    },
    "identities": {
     "type": "array",
     "title": "Consumer AD Groups",
     "description": "List of groups that are intended consumers of the data",
     "items": {
      "type": "string"
     },
     "default": null
    },
    "rowFilters": {
     "type": "array",
     "title": "Row Data Filters",
     "description": "List of filters that define what subset of the data is accessible to the consumers",
     "items": {
      "$ref": "#/definitions/rowFilter"
     },
     "default": null
    }
   }
  },
  "contractSecurity": {
   "type": "object",
   "description": "Security and access policy expectations",
   "properties": {
    "inherited": {
     "description": "If the property is inherited from the Data Product",
     "type": "boolean"
    },
    "dataClassification": {
     "type": "string",
     "title": "Data Classification",
     "description": "Expected data classification (e.g. Confidential, PII, etc.)"
    },
    "policies": {
     "type": "array",
     "title": "Data Consumers",
     "description": "Intended consumers of the data (e.g. internal teams, external partners, etc.)",
     "items": {
      "$ref": "#/definitions/policy"
     }
    }
   }
  },
  "contractSLA": {
   "type": "object",
   "description": "Service Level Agreement expectations (timeliness, availability, etc.)",
   "properties": {
    "inherited": {
     "description": "If the property is inherited from the Data Product",
     "type": "boolean"
    },
    "refreshFrequency": {
     "type": "object",
     "title": "Refresh Frequency",
     "properties": {
      "interval": {
       "type": "integer"
      },
      "unit": {
       "type": "string",
       "enum": [
        "hour",
        "day",
        "week",
        "month",
        "year"
       ]
      }
     },
     "description": "Expected frequency of data updates (e.g. every 1 day)",
     "required": [
      "interval",
      "unit"
     ]
    },
    "maxLatency": {
     "type": "object",
     "title": "Maximum Latency",
     "properties": {
      "value": {
       "type": "integer"
      },
      "unit": {
       "type": "string",
       "enum": [
        "minute",
        "hour",
        "day"
       ]
      }
     },
     "description": "Maximum acceptable latency between data generation and availability (e.g. 4 hours)",
     "required": [
      "value",
      "unit"
     ]
    },
    "availabilityTime": {
     "title": "Availability Time",
     "type": "string",
     "description": "Time of day by which data is expected to be available (e.g. \"09:00 UTC\")"
    },
    "timezone": {
     "type": "string",
     "title": "Timezone",
     "description": "Timezone for the availability time. UTC by default.",
     "enum": [
      "…36 GMT offsets, e.g. \"GMT+07:00 (Asia/Bangkok)\"…"
     ],
     "default": "GMT+00:00 (Europe/London)"
    },
    "retention": {
     "type": "object",
     "title": "Data Retention Period",
     "properties": {
      "period": {
       "type": "integer"
      },
      "unit": {
       "type": "string",
       "enum": [
        "day",
        "week",
        "month",
        "year"
       ]
      }
     },
     "description": "How long the data is retained (if relevant)",
     "required": [
      "period",
      "unit"
     ]
    },
    "columnName": {
     "type": "string",
     "title": "Column Name",
     "description": "Column that represents the refresh time of the data (if applicable)"
    }
   }
  },
  "termsOfUse": {
   "type": "object",
   "description": "Terms of use for the data contract for both human and AI agents consumption.",
   "properties": {
    "content": {
     "$ref": "../../type/basic.json#/definitions/markdown"
    },
    "inherited": {
     "description": "If the property is inherited from the Data Product",
     "type": "boolean"
    }
   }
  }
 },
 "properties": {
  "id": {
   "description": "Unique identifier of this data contract instance.",
   "$ref": "../../type/basic.json#/definitions/uuid"
  },
  "name": {
   "description": "Name of the data contract.",
   "$ref": "../../type/basic.json#/definitions/entityName"
  },
  "displayName": {
   "description": "Display name of the data contract.",
   "type": "string"
  },
  "fullyQualifiedName": {
   "description": "Fully qualified name of the data contract.",
   "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "description": {
   "description": "Description of the data contract.",
   "$ref": "../../type/basic.json#/definitions/markdown"
  },
  "version": {
   "description": "Metadata version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/entityVersion"
  },
  "entityStatus": {
   "description": "Approval status of the data contract.",
   "$ref": "../../type/status.json"
  },
  "entity": {
   "description": "Reference to the data entity (table, topic, etc.) this contract applies to.",
   "$ref": "../../type/entityReference.json"
  },
  "testSuite": {
   "description": "Reference to the test suite that contains tests related to this data contract.",
   "$ref": "../../type/entityReference.json"
  },
  "schema": {
   "description": "Schema definition for the data contract.",
   "type": "array",
   "items": {
    "$ref": "./table.json#/definitions/column"
   },
   "default": null
  },
  "semantics": {
   "description": "Semantics rules defined in the data contract.",
   "type": "array",
   "items": {
    "$ref": "../../type/basic.json#/definitions/semanticsRule"
   },
   "default": null
  },
  "termsOfUse": {
   "description": "Terms of use for the data contract for both human and AI agents consumption.",
   "$ref": "#/definitions/termsOfUse",
   "default": null
  },
  "security": {
   "title": "Contract Security",
   "description": "Security and access policy expectations defined in the data contract.",
   "$ref": "#/definitions/contractSecurity",
   "default": null
  },
  "sla": {
   "title": "Contract SLA",
   "description": "Service Level Agreement expectations defined in the data contract.",
   "$ref": "#/definitions/contractSLA",
   "default": null
  },
  "qualityExpectations": {
   "description": "Quality expectations defined in the data contract.",
   "type": "array",
   "items": {
    "$ref": "../../type/entityReference.json"
   },
   "default": null
  },
  "odcsQualityRules": {
   "description": "ODCS quality rules stored during import for round-trip compatibility with ODCS export.",
   "type": "array",
   "items": {
    "$ref": "../datacontract/odcs/odcsDataContract.json#/definitions/odcsQualityRule"
   },
   "default": null
  },
  "odcsElementExtensions": {
   "description": "ODCS schema element attributes with no OpenMetadata equivalent, stored during import for round-trip compatibility with ODCS export.",
   "type": "array",
   "items": {
    "$ref": "../datacontract/odcs/odcsDataContract.json#/definitions/odcsElementExtension"
   },
   "default": null
  },
  "contractUpdates": {
   "description": "History of updates to the data contract.",
   "type": "array",
   "items": {
    "$ref": "#/definitions/contractUpdate"
   },
   "default": null
  },
  "owners": {
   "description": "Owners of this data contract.",
   "$ref": "../../type/entityReferenceList.json",
   "default": null
  },
  "reviewers": {
   "description": "User references of the reviewers for this data contract.",
   "$ref": "../../type/entityReferenceList.json",
   "default": null
  },
  "effectiveFrom": {
   "description": "Date from which this data contract is effective.",
   "$ref": "../../type/basic.json#/definitions/dateTime",
   "default": null
  },
  "effectiveUntil": {
   "description": "Date until which this data contract is effective.",
   "$ref": "../../type/basic.json#/definitions/dateTime",
   "default": null
  },
  "changeDescription": {
   "description": "Change that led to this version of the entity.",
   "$ref": "../../type/entityHistory.json#/definitions/changeDescription"
  },
  "sourceUrl": {
   "description": "Source URL of the data contract.",
   "$ref": "../../type/basic.json#/definitions/sourceUrl"
  },
  "latestResult": {
   "description": "Latest validation result for this data contract.",
   "type": "object",
   "properties": {
    "timestamp": {
     "$ref": "../../type/basic.json#/definitions/timestamp"
    },
    "status": {
     "$ref": "../../type/contractExecutionStatus.json"
    },
    "message": {
     "type": "string"
    },
    "resultId": {
     "$ref": "../../type/basic.json#/definitions/uuid"
    }
   },
   "additionalProperties": false
  },
  "inherited": {
   "description": "Indicates whether this data contract is inherited from a parent entity.",
   "type": "boolean",
   "default": false
  }
 },
 "required": [
  "id",
  "name",
  "entity"
 ]
}
```

#### datacontract/dataContractResult.json (properties)

Source: `openmetadata-spec/src/main/resources/json/schema/entity/datacontract/dataContractResult.json`

```json
{
 "id": {
  "description": "Unique Data Contract validation execution identifier of this data contract result instance.",
  "$ref": "../../type/basic.json#/definitions/uuid"
 },
 "dataContractFQN": {
  "description": "Fully qualified name of the data contract.",
  "$ref": "../../type/basic.json#/definitions/fullyQualifiedEntityName"
 },
 "timestamp": {
  "description": "Timestamp when the data contract was executed.",
  "$ref": "../../type/basic.json#/definitions/timestamp"
 },
 "contractExecutionStatus": {
  "description": "Overall status of the contract execution.",
  "$ref": "../../type/contractExecutionStatus.json"
 },
 "result": {
  "description": "Detailed result of the data contract execution.",
  "type": "string"
 },
 "schemaValidation": {
  "description": "Schema validation details.",
  "$ref": "schemaValidation.json",
  "default": null
 },
 "semanticsValidation": {
  "description": "Semantics validation details.",
  "$ref": "semanticsValidation.json",
  "default": null
 },
 "qualityValidation": {
  "description": "Quality expectations validation details.",
  "$ref": "qualityValidation.json",
  "default": null
 },
 "slaValidation": {
  "description": "SLA validation details.",
  "$ref": "slaValidation.json",
  "default": null
 },
 "incidentId": {
  "description": "Incident ID if the contract execution failed and an incident was created.",
  "$ref": "../../type/basic.json#/definitions/uuid"
 },
 "executionTime": {
  "description": "Time taken to execute the contract validation in milliseconds.",
  "$ref": "../../type/basic.json#/definitions/timestamp"
 }
}
```

#### entityLineage.json — edge, lineageDetails, columnLineage

Source: `openmetadata-spec/src/main/resources/json/schema/type/entityLineage.json`

```json
{
 "edge": {
  "description": "Edge in the lineage graph from one entity to another by entity IDs.",
  "type": "object",
  "javaType": "org.openmetadata.schema.type.Edge",
  "properties": {
   "fromEntity": {
    "description": "From entity that is upstream of lineage edge.",
    "$ref": "basic.json#/definitions/uuid"
   },
   "toEntity": {
    "description": "To entity that is downstream of lineage edge.",
    "$ref": "basic.json#/definitions/uuid"
   },
   "description": {
    "$ref": "basic.json#/definitions/markdown"
   },
   "lineageDetails": {
    "description": "Optional lineageDetails provided only for table to table lineage edge.",
    "$ref": "#/definitions/lineageDetails"
   }
  },
  "required": [
   "fromEntity",
   "toEntity"
  ],
  "additionalProperties": false
 },
 "lineageDetails": {
  "description": "Lineage details including sqlQuery + pipeline + columnLineage.",
  "type": "object",
  "properties": {
   "sqlQuery": {
    "description": "SQL used for transformation.",
    "$ref": "../type/basic.json#/definitions/sqlQuery"
   },
   "columnsLineage": {
    "description": "Lineage information of how upstream columns were combined to get downstream column.",
    "type": "array",
    "items": {
     "$ref": "#/definitions/columnLineage"
    }
   },
   "pipeline": {
    "description": "Pipeline where the sqlQuery is periodically run.",
    "$ref": "../type/entityReference.json"
   },
   "description": {
    "description": "description of lineage",
    "type": "string"
   },
   "source": {
    "description": "Lineage type describes how a lineage was created.",
    "type": "string",
    "enum": [
     "Manual",
     "ViewLineage",
     "QueryLineage",
     "PipelineLineage",
     "DashboardLineage",
     "DbtLineage",
     "SparkLineage",
     "OpenLineage",
     "ExternalTableLineage",
     "CrossDatabaseLineage",
     "ChildAssets"
    ],
    "default": "Manual"
   },
   "createdAt": {
    "description": "Last update time corresponding to the new version of the entity in Unix epoch time milliseconds.",
    "$ref": "../type/basic.json#/definitions/timestamp"
   },
   "createdBy": {
    "description": "User who created the node.",
    "type": "string"
   },
   "updatedAt": {
    "description": "Last update time corresponding to the new version of the entity in Unix epoch time milliseconds.",
    "$ref": "../type/basic.json#/definitions/timestamp"
   },
   "updatedBy": {
    "description": "User who made the update.",
    "type": "string"
   },
   "assetEdges": {
    "description": "Asset count in case of child assets lineage.",
    "type": "integer",
    "default": null
   },
   "tempLineageTables": {
    "description": "Lineage path through temporary/intermediate tables. Each element represents a hop with fromEntity and toEntity fields.",
    "type": "array",
    "items": {
     "$ref": "#/definitions/tempLineageTable"
    },
    "default": null
   }
  }
 },
 "columnLineage": {
  "type": "object",
  "properties": {
   "fromColumns": {
    "description": "One or more source columns identified by fully qualified column name used by transformation function to create destination column.",
    "type": "array",
    "items": {
     "$ref": "../type/basic.json#/definitions/fullyQualifiedEntityName"
    }
   },
   "toColumn": {
    "description": "Destination column identified by fully qualified column name created by the transformation of source columns.",
    "$ref": "../type/basic.json#/definitions/fullyQualifiedEntityName"
   },
   "function": {
    "description": "Transformation function applied to source columns to create destination column. That is `function(fromColumns) -> toColumn`.",
    "$ref": "../type/basic.json#/definitions/sqlFunction"
   }
  }
 },
 "tempLineageTable": {
  "description": "A single hop in a temporary table lineage path.",
  "type": "object",
  "properties": {
   "fromEntity": {
    "description": "Source entity or table name for this hop.",
    "type": "string"
   },
   "toEntity": {
    "description": "Target entity or table name for this hop.",
    "type": "string"
   }
  },
  "required": [
   "fromEntity",
   "toEntity"
  ]
 }
}
```

#### tests/testCase.json (properties, required)

Source: `openmetadata-spec/src/main/resources/json/schema/tests/testCase.json`. Common envelope fields omitted for length: `createdBy, updatedAt, updatedBy, href, incrementalChangeDescription, deleted, followers` (same meaning on every entity)

```json
{
 "definitions": {
  "testCaseParameterValue": {
   "type": "object",
   "javaType": "org.openmetadata.schema.tests.TestCaseParameterValue",
   "description": "This schema defines the parameter values that can be passed for a Test Case.",
   "properties": {
    "name": {
     "description": "name of the parameter. Must match the parameter names in testCaseParameterDefinition",
     "type": "string"
    },
    "value": {
     "description": "value to be passed for the Parameters. These are input from Users. We capture this in string and convert during the runtime.",
     "type": "string"
    }
   }
  }
 },
 "properties": {
  "id": {
   "description": "Unique identifier of this table instance.",
   "$ref": "../type/basic.json#/definitions/uuid"
  },
  "name": {
   "description": "Name that identifies this test case.",
   "$ref": "../type/basic.json#/definitions/testCaseEntityName"
  },
  "displayName": {
   "description": "Display Name that identifies this test.",
   "type": "string"
  },
  "fullyQualifiedName": {
   "description": "FullyQualifiedName same as `name`.",
   "$ref": "../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "description": {
   "description": "Description of the testcase.",
   "$ref": "../type/basic.json#/definitions/markdown"
  },
  "testDefinition": {
   "description": "Test definition that this test case is based on.",
   "$ref": "../type/entityReference.json"
  },
  "entityLink": {
   "description": "Link to the entity that this test case is testing.",
   "$ref": "../type/basic.json#/definitions/entityLink"
  },
  "entityFQN": {
   "type": "string"
  },
  "testSuite": {
   "description": "Basic Test Suite that this test case belongs to.",
   "$ref": "../type/entityReference.json"
  },
  "testSuites": {
   "type": "array",
   "description": "Basic and Logical Test Suites this test case belongs to",
   "items": {
    "$ref": "./testSuite.json"
   }
  },
  "parameterValues": {
   "type": "array",
   "items": {
    "$ref": "#/definitions/testCaseParameterValue"
   }
  },
  "dataQualityDimension": {
   "description": "Data quality dimension this test case belongs to. When not set at creation time, it defaults to the dimension of the test definition. Any dimension registered in Settings > Preferences > Data Quality can be used, system and custom alike.",
   "$ref": "../type/entityReference.json"
  },
  "testCaseResult": {
   "description": "Latest test case result obtained for this test case.",
   "$ref": "./basic.json#/definitions/testCaseResult"
  },
  "testCaseStatus": {
   "description": "Status of Test Case run.",
   "$ref": "./basic.json#/definitions/testCaseStatus"
  },
  "version": {
   "description": "Metadata version of the entity.",
   "$ref": "../type/entityHistory.json#/definitions/entityVersion"
  },
  "owners": {
   "description": "Owners of this Pipeline.",
   "$ref": "../type/entityReferenceList.json",
   "default": null
  },
  "changeDescription": {
   "description": "Change that lead to this version of the entity.",
   "$ref": "../type/entityHistory.json#/definitions/changeDescription"
  },
  "computePassedFailedRowCount": {
   "description": "Compute the passed and failed row count for the test case.",
   "type": "boolean",
   "default": false
  },
  "autoCloseIncident": {
   "description": "Automatically resolve an open incident when a subsequent test result succeeds.",
   "type": "boolean",
   "default": false
  },
  "incidentId": {
   "description": "Reference to an ongoing Incident ID (stateId) for this test case.",
   "$ref": "../type/basic.json#/definitions/uuid"
  },
  "incidentStatus": {
   "description": "Latest incident resolution status for this test case. Inlined so clients can render the current incident status without an additional lookup per test case.",
   "$ref": "./testCaseResolutionStatus.json"
  },
  "failedRowsSample": {
   "description": "Sample of failed rows for this test case.",
   "$ref": "../entity/data/table.json#/definitions/tableData"
  },
  "inspectionQuery": {
   "description": "SQL query to retrieve the failed rows for this test case.",
   "$ref": "../type/basic.json#/definitions/sqlQuery"
  },
  "domains": {
   "description": "Domains the test case belongs to. When not set, the test case inherits the domain from the table it belongs to.",
   "$ref": "../type/entityReferenceList.json"
  },
  "dataProducts": {
   "description": "List of data products this test case is part of. When not set, the test case inherits the data products from the table it belongs to.",
   "$ref": "../type/entityReferenceList.json"
  },
  "useDynamicAssertion": {
   "description": "If the test definition supports it, use dynamic assertion to evaluate the test case.",
   "type": "boolean",
   "default": false
  },
  "tags": {
   "description": "Tags for this test case. This is an inherited field from the parent entity and is not set directly on the test case.",
   "type": "array",
   "items": {
    "$ref": "../type/tagLabel.json"
   },
   "default": []
  },
  "dimensionColumns": {
   "description": "List of columns to group test results by dimensions. When specified, the test will be executed both overall and grouped by these columns to provide fine-grained data quality insights.",
   "type": "array",
   "items": {
    "type": "string"
   },
   "default": []
  },
  "topDimensions": {
   "description": "Number of top dimension values to show before grouping the rest as Others. Controls the cardinality of dimensional test results. Defaults to 5 when not specified.",
   "type": "integer",
   "minimum": 1,
   "maximum": 50,
   "default": 5
  },
  "entityStatus": {
   "description": "Current status of the test case.",
   "$ref": "../type/status.json"
  },
  "reviewers": {
   "description": "List of reviewers for this entity.",
   "$ref": "../type/entityReferenceList.json",
   "default": null
  }
 },
 "required": [
  "name",
  "testDefinition",
  "entityLink",
  "testSuite"
 ]
}
```

#### tests/basic.json#/definitions/testCaseResult

Source: `openmetadata-spec/src/main/resources/json/schema/tests/basic.json`

```json
{
 "description": "Schema to capture test case result.",
 "javaType": "org.openmetadata.schema.tests.type.TestCaseResult",
 "type": "object",
 "properties": {
  "id": {
   "description": "Unique identifier of this failure instance",
   "$ref": "../type/basic.json#/definitions/uuid"
  },
  "testCaseFQN": {
   "description": "Fully qualified name of the test case.",
   "$ref": "../type/basic.json#/definitions/fullyQualifiedEntityName"
  },
  "timestamp": {
   "description": "Data one which test case result is taken.",
   "$ref": "../type/basic.json#/definitions/timestamp"
  },
  "testCaseStatus": {
   "description": "Status of Test Case run.",
   "$ref": "#/definitions/testCaseStatus"
  },
  "result": {
   "description": "Details of test case results.",
   "type": "string"
  },
  "sampleData": {
   "description": "sample data to capture rows/columns that didn't match the expressed testcase.",
   "type": "string"
  },
  "testResultValue": {
   "type": "array",
   "items": {
    "$ref": "#/definitions/testResultValue"
   }
  },
  "passedRows": {
   "description": "Number of rows that passed.",
   "type": "integer",
   "existingJavaType": "java.lang.Long"
  },
  "failedRows": {
   "description": "Number of rows that failed.",
   "type": "integer",
   "existingJavaType": "java.lang.Long"
  },
  "passedRowsPercentage": {
   "description": "Percentage of rows that passed.",
   "type": "number"
  },
  "failedRowsPercentage": {
   "description": "Percentage of rows that failed.",
   "type": "number"
  },
  "incidentId": {
   "description": "Incident State ID associated with this result. This association happens when the result is created, and will stay there even when the incident is resolved.",
   "$ref": "../type/basic.json#/definitions/uuid"
  },
  "maxBound": {
   "description": "Upper bound limit for the test case result as defined in the test definition.",
   "type": "number"
  },
  "minBound": {
   "description": "Lower bound limit for the test case result as defined in the test definition.",
   "type": "number"
  },
  "testCase": {
   "description": "Test case that this result is for.",
   "$ref": "../type/entityReference.json"
  },
  "testDefinition": {
   "description": "Test definition that this result is for.",
   "$ref": "../type/entityReference.json"
  },
  "dimensionResults": {
   "description": "List of dimensional test results. Only populated when the test case has dimensionColumns specified.",
   "type": "array",
   "items": {
    "$ref": "#/definitions/testCaseDimensionResult"
   }
  },
  "duration": {
   "description": "Wall-clock time the test case run took, in milliseconds.",
   "type": "number"
  },
  "errorDetails": {
   "description": "Structured error details when the run was aborted by an error.",
   "$ref": "#/definitions/testCaseErrorDetails"
  },
  "evaluationScope": {
   "description": "Which rows the run was measured on. Absent for a run that recorded no scope.",
   "$ref": "#/definitions/testCaseEvaluationScope"
  }
 },
 "required": [
  "timestamp"
 ]
}
```

#### tests/testCaseResolutionStatus.json (incident lifecycle) — definitions

Source: `openmetadata-spec/src/main/resources/json/schema/tests/testCaseResolutionStatus.json`

```json
{
 "definitions": {
  "testCaseResolutionStatusTypes": {
   "description": "Test case resolution status type.",
   "javaType": "org.openmetadata.schema.tests.type.TestCaseResolutionStatusTypes",
   "type": "string",
   "enum": [
    "New",
    "Ack",
    "Assigned",
    "Resolved"
   ]
  },
  "severities": {
   "description": "Test case resolution status type.",
   "javaType": "org.openmetadata.schema.tests.type.Severity",
   "type": "string",
   "enum": [
    "Severity1",
    "Severity2",
    "Severity3",
    "Severity4",
    "Severity5"
   ]
  }
 },
 "property_names": [
  "id",
  "stateId",
  "timestamp",
  "testCaseResolutionStatusType",
  "testCaseResolutionStatusDetails",
  "updatedBy",
  "updatedAt",
  "testCaseReference",
  "severity",
  "failureSummary",
  "metrics"
 ]
}
```

#### type/tagLabel.json (how any tag/term is attached)

Source: `openmetadata-spec/src/main/resources/json/schema/type/tagLabel.json`

```json
{
 "definitions": {
  "tagFQN": {
   "type": "string"
  },
  "TagSource": {
   "type": "string",
   "default": "Classification",
   "enum": [
    "Classification",
    "Glossary"
   ]
  }
 },
 "properties": {
  "tagFQN": {
   "$ref": "#/definitions/tagFQN"
  },
  "name": {
   "description": "Name of the tag or glossary term.",
   "type": "string"
  },
  "displayName": {
   "description": "Display Name that identifies this tag.",
   "type": "string"
  },
  "description": {
   "description": "Description for the tag label.",
   "$ref": "../type/basic.json#/definitions/markdown"
  },
  "style": {
   "$ref": "../type/basic.json#/definitions/style"
  },
  "source": {
   "description": "Label is from Tags or Glossary.",
   "$ref": "#/definitions/TagSource"
  },
  "labelType": {
   "description": "Label type describes how a tag label was applied. 'Manual' indicates the tag label was applied by a person. 'Derived' indicates a tag label was derived using the associated tag relationship (see Classification.json for more details). 'Propagated` indicates a tag label was propagated from upstream based on lineage. 'Automated' is used when a tool was used to determine the tag label.",
   "type": "string",
   "enum": [
    "Manual",
    "Propagated",
    "Automated",
    "Derived",
    "Generated"
   ],
   "default": "Manual"
  },
  "state": {
   "description": "'Suggested' state is used when a tag label is suggested by users or tools. Owner of the entity must confirm the suggested labels before it is marked as 'Confirmed'.",
   "type": "string",
   "enum": [
    "Suggested",
    "Confirmed"
   ],
   "default": "Confirmed"
  },
  "href": {
   "description": "Link to the tag resource.",
   "$ref": "basic.json#/definitions/href"
  },
  "reason": {
   "type": "string",
   "description": "An explanation of why this tag was proposed, specially for autoclassification tags"
  },
  "appliedAt": {
   "type": "string",
   "format": "date-time",
   "description": "Timestamp when this tag was applied in ISO 8601 format"
  },
  "appliedBy": {
   "type": "string",
   "description": "Who it is that applied this tag (e.g: a bot, AI or a human)"
  },
  "metadata": {
   "description": "Additional metadata associated with this tag label, such as recognizer information for automatically applied tags.",
   "$ref": "tagLabelMetadata.json"
  }
 },
 "required": [
  "tagFQN",
  "source",
  "labelType",
  "state"
 ]
}
```

#### type/aiContext.json — knowledgeItem, dataQuality, observability (the agent-facing envelope)

Source: `openmetadata-spec/src/main/resources/json/schema/type/aiContext.json`

```json
{
 "knowledgeItem": {
  "type": "object",
  "javaType": "org.openmetadata.schema.type.aicontext.KnowledgeItem",
  "description": "A single piece of business knowledge attached to the asset (glossary term definition, metric definition, or Context Center article/pill).",
  "properties": {
   "id": {
    "description": "Unique identifier of the knowledge entity.",
    "$ref": "basic.json#/definitions/uuid"
   },
   "type": {
    "description": "The entity type of the knowledge item.",
    "type": "string",
    "enum": [
     "glossaryTerm",
     "metric",
     "page",
     "contextMemory"
    ]
   },
   "name": {
    "type": "string"
   },
   "displayName": {
    "type": "string"
   },
   "fullyQualifiedName": {
    "$ref": "basic.json#/definitions/fullyQualifiedEntityName"
   },
   "content": {
    "description": "The definition, business rule, metric expression, or article body for this item. May be a bounded lead excerpt when contentTruncated is true, or null when omitted to stay within the context budget.",
    "type": "string"
   },
   "contentTruncated": {
    "description": "True when content is a lead excerpt or has been omitted to fit the context budget; fetch the full body via the get_knowledge_content tool using this item's fullyQualifiedName.",
    "type": "boolean",
    "default": false
   },
   "stale": {
    "description": "True when read-time trust signals indicate this item may no longer reflect the asset it is attached to (e.g. the asset changed after the knowledge was last updated, or the asset's data-quality tests are failing). Absent means no staleness signal.",
    "type": "boolean",
    "default": false
   },
   "staleReasons": {
    "description": "Machine-readable reasons for the stale flag (e.g. assetUpdated, dataQualityFailing).",
    "type": "array",
    "items": {
     "type": "string"
    }
   }
  },
  "additionalProperties": false
 },
 "joinHint": {
  "type": "object",
  "javaType": "org.openmetadata.schema.type.aicontext.JoinHint",
  "description": "An empirical join between a column of this table and a column of another table, derived from observed query usage. A strong, data-driven signal for how tables are joined in practice.",
  "properties": {
   "column": {
    "description": "Local column name on this table.",
    "type": "string"
   },
   "joinedWith": {
    "description": "Fully qualified name of the column this column is frequently joined with.",
    "$ref": "basic.json#/definitions/fullyQualifiedEntityName"
   },
   "joinCount": {
    "description": "Number of observed joins over the recorded window.",
    "type": "integer"
   }
  },
  "additionalProperties": false
 },
 "dataQuality": {
  "type": "object",
  "javaType": "org.openmetadata.schema.type.aicontext.DataQuality",
  "description": "Data-quality standing of the asset, so an agent can caveat its answer when tests are failing or an incident is open.",
  "properties": {
   "total": {
    "type": "integer"
   },
   "passed": {
    "type": "integer"
   },
   "failed": {
    "type": "integer"
   },
   "aborted": {
    "type": "integer"
   },
   "openIncidents": {
    "description": "Number of currently-open (unresolved) data-quality incidents on the asset.",
    "type": "integer"
   }
  },
  "additionalProperties": false
 },
 "observability": {
  "type": "object",
  "javaType": "org.openmetadata.schema.type.aicontext.Observability",
  "description": "Runtime signals about the asset: its profiled shape (for constructing accurate queries) and its data-quality/incident standing (for qualifying answers). Volatile — assembled on read, not part of the semantic embedding.",
  "properties": {
   "rowCount": {
    "description": "Latest profiled row count.",
    "type": "number"
   },
   "profiledAt": {
    "description": "Timestamp of the latest profile run.",
    "$ref": "basic.json#/definitions/timestamp"
   },
   "profileSample": {
    "description": "Sample size the profile ran on — a percentage when profileSampleType is PERCENTAGE, otherwise an absolute row count. Absent when the profile ran on the full dataset.",
    "type": "number"
   },
   "profileSampleType": {
    "description": "Interpretation of profileSample: PERCENTAGE or ROWS.",
    "type": "string"
   },
   "columnProfiles": {
    "type": "array",
    "items": {
     "$ref": "#/definitions/columnProfileSummary"
    }
   },
   "dataQuality": {
    "$ref": "#/definitions/dataQuality"
   }
  },
  "additionalProperties": false
 },
 "conceptBinding": {
  "type": "object",
  "javaType": "org.openmetadata.schema.type.aicontext.ConceptBinding",
  "description": "One asset or column that realizes a glossary term or supplies a metric. Profiles and samples are permission checked and PII masked.",
  "properties": {
   "assetFqn": {
    "$ref": "basic.json#/definitions/fullyQualifiedEntityName"
   },
   "assetType": {
    "type": "string"
   },
   "column": {
    "description": "Fully qualified column name. Absent for an asset-level binding.",
    "$ref": "basic.json#/definitions/fullyQualifiedEntityName"
   },
   "dataType": {
    "type": "string"
   },
   "rowCount": {
    "description": "Latest profiled row count of the bound table, repeated on each of its bindings. Present only for tables whose data profile the caller may view.",
    "type": "number"
   },
   "profile": {
    "$ref": "#/definitions/columnProfileSummary"
   },
   "sampleValues": {
    "description": "Up to ten stored sample values for this column, available only with VIEW_SAMPLE_DATA and existing PII masking. These are representative samples, not the complete value set.",
    "type": "array",
    "maxItems": 10,
    "items": {}
   }
  },
  "required": [
   "assetFqn",
   "assetType"
  ],
  "additionalProperties": false
 },
 "_top_level_properties": [
  "id",
  "name",
  "fullyQualifiedName",
  "entityType",
  "service",
  "serviceType",
  "displayName",
  "description",
  "owners",
  "resource",
  "tags",
  "glossaryTerms",
  "metrics",
  "articles",
  "upstream",
  "downstream",
  "upstreamEdges",
  "downstreamEdges",
  "assetContext",
  "observability",
  "fingerprint",
  "generatedAt"
 ]
}
```

#### governance/changeRequest/mutationOp.json (per-field proposed change)

Source: `openmetadata-spec/src/main/resources/json/schema/governance/changeRequest/mutationOp.json`

```json
{
 "$id": "https://open-metadata.org/schema/governance/changeRequest/mutationOp.json",
 "$schema": "http://json-schema.org/draft-07/schema#",
 "title": "MutationOp",
 "description": "One normalized operation of a proposed metadata change, recorded with the published value it was computed against.",
 "type": "object",
 "javaType": "org.openmetadata.schema.governance.changeRequest.MutationOp",
 "definitions": {
  "mutationOpType": {
   "javaType": "org.openmetadata.schema.governance.changeRequest.MutationOpType",
   "description": "set replaces a top-level field value; add and remove change one element of a list field identified by key.",
   "type": "string",
   "enum": [
    "set",
    "add",
    "remove"
   ]
  },
  "changeOutcome": {
   "javaType": "org.openmetadata.schema.governance.changeRequest.ChangeOutcome",
   "description": "Where one change of the active revision stands: published, dropped by its reviewers, published by another change, not published because its reviewers could not agree, superseded by a newer published value of its field, or still waiting for a decision.",
   "type": "string",
   "enum": [
    "Pending",
    "Applied",
    "Rejected",
    "AlreadyPublished",
    "NotAgreed",
    "Superseded"
   ]
  }
 },
 "properties": {
  "op": {
   "$ref": "#/definitions/mutationOpType"
  },
  "outcome": {
   "description": "Reported with the active revision of a change request; not stored with the revision.",
   "$ref": "#/definitions/changeOutcome"
  },
  "field": {
   "description": "Top-level entity field name.",
   "type": "string"
  },
  "key": {
   "description": "Element identity for add/remove: the string value itself, a tag FQN, a related glossary term with its relation type, an external reference endpoint with its name, or an entity reference id.",
   "type": "string"
  },
  "baseValue": {
   "description": "JSON of the published field value this op was computed against. Set ops only.",
   "type": "string"
  },
  "value": {
   "description": "JSON of the proposed field value (set) or of the element being added/removed.",
   "type": "string"
  },
  "gated": {
   "description": "True when an approval workflow gates this field; false for a field carried along in a staged request.",
   "type": "boolean",
   "default": true
  }
 },
 "required": [
  "op",
  "field"
 ],
 "additionalProperties": false
}
```
