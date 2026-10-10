import { pathId, v1Get, v1Send, type Page } from './client';
import type { Source } from './types';

/*
 * Dữ liệu · Nguồn (P04) and Tài liệu (P05): sources, the connect flow, ingestion jobs, file
 * uploads, collections and their documents. Paths follow "Đặc tả API" (/v1/<plural>, `:verb`
 * actions, cursor pages). The spec has catalog.source and catalog.dataset; everything the
 * P04/P05 screens need beyond them is marked PROPOSAL, as in types.ts, and served by
 * mocks/sources.ts until the backend has it.
 */

type Uuid = string;
type Timestamp = string;

const ALL = { page_size: 500 };

/* ─── Nguồn: catalog.source as the P04 list draws it ──────────────────── */

export interface DataSpaceRef {
  id: Uuid;
  name: string;
}

/** GET /v1/sources, with the fields "P04 · Nguồn · Danh sách" shows. */
export interface SourceRow extends Source {
  /** PROPOSAL: connector code (oracle, sqlserver, rest, kafka, folder…) and its display name. */
  connector: string;
  connector_label: string;
  /** PROPOSAL: where it lives, shown in mono under the name ("core-db.noibo"). */
  endpoint: string;
  /** PROPOSAL: how an ingest source refreshes ("CDC", "hằng ngày", "2 giờ", "liên tục", "tệp mới"). */
  refresh_text: string | null;
  /** PROPOSAL: the short status sentence ("Đang đồng bộ", "Cần sửa · đăng nhập lỗi"). */
  status_label: string;
  /** PROPOSAL: the source changed its structure and a person must look before loading goes on. */
  schema_drift: boolean;
  /** PROPOSAL: the open structure change awaiting review ("Lệch cấu trúc · Duyệt thay đổi"). */
  drift_id: string | null;
  /** PROPOSAL: replication lag of a syncing source, and records held back for review. */
  lag_seconds: number | null;
  held_records: number | null;
  /** PROPOSAL: the Data Spaces that use its tables. */
  data_spaces: DataSpaceRef[];
}

/** A table of a source (catalog.dataset of that source). */
export interface SourceTable {
  id: Uuid;
  name: string;
  /** PROPOSAL: business description, row and column counts, last refresh. */
  description: string | null;
  row_count: number | null;
  column_count: number | null;
  refreshed_at: Timestamp | null;
}

/** GET /v1/sources/{id}: the row plus its tables (PROPOSAL: `tables` embedded). */
export interface SourceDetail extends SourceRow {
  tables: SourceTable[];
}

export const listSourceRows = () => v1Get<Page<SourceRow>>('/sources', ALL);
export const getSource = async (id: string) => v1Get<SourceDetail>(`/sources/${pathId(id)}`);

/* ─── Hồ sơ bảng: the profile of one table ────────────────────────────── */

export interface ColumnProfile {
  name: string;
  data_type: string;
  /** Share of empty values, 0..1; null when the column is not profiled (personal data). */
  null_ratio: number | null;
  /** Distinct values; `unique` = every row differs; null = not computed. */
  distinct: number | 'unique' | null;
  /** Histogram heights 0..1, left to right; null when the column has none. */
  histogram: number[] | null;
  /** "duy nhất", "che" — what stands in for a histogram. */
  histogram_note: string | null;
  note: string | null;
  /** The note is a warning (e.g. 62% empty). */
  warn: boolean;
}

/** PROPOSAL: GET /v1/datasets/{id}/profile (catalog.dataset + column profile + agent review). */
export interface TableProfile {
  id: Uuid;
  name: string;
  source: { id: Uuid; name: string };
  row_count: number;
  column_count: number;
  refreshed_at: Timestamp | null;
  /** "03:00 Chủ nhật trên mẫu 1%" */
  profiled_text: string;
  columns: ColumnProfile[];
  /** The Data Prep agent's remarks, each one sentence; null when it has none. */
  review: { agent_name: string; remarks: string[]; footnote: string } | null;
}

export const getTableProfile = async (id: string) =>
  v1Get<TableProfile>(`/datasets/${pathId(id)}/profile`);

/** PROPOSAL: POST /v1/datasets/{id}:profile — profile again now. */
export const reprofileTable = async (id: string) =>
  v1Send<{ status: 'queued' }>('POST', `/datasets/${pathId(id)}:profile`);

/* ─── Kết nối nguồn: connectors and the five-step flow ────────────────── */

export type ConnectorCategory = 'database' | 'warehouse' | 'app' | 'file' | 'document';

/** PROPOSAL: GET /v1/connectors — the catalogue on "Kết nối dữ liệu". */
export interface Connector {
  code: string;
  name: string;
  category: ConnectorCategory;
  /** A subtitle that replaces the category name ("Bằng SDK hoặc MCP"). */
  subtitle: string | null;
  connected_count: number;
  badge: 'new' | 'upload' | 'engineering' | null;
  /** Labels of the connection form; null for connectors without a host form. */
  form: { database_label: string; default_port: number } | null;
  /** What the DBA prepares, with {host} {port} {user} placeholders, and sample grants. */
  prep_notes: string[];
  sample_sql: string | null;
}

export const listConnectors = () => v1Get<Page<Connector>>('/connectors', ALL);

/** A secret reference the connection can use (governance secrets; never the secret itself). */
export interface SecretRef {
  ref: string;
  label: string;
}

/** PROPOSAL: GET /v1/secret-refs?kind= — references only, the value stays in OpenBao. */
export const listSecretRefs = (kind: 'database' | 'api' = 'database') =>
  v1Get<Page<SecretRef>>('/secret-refs', { ...ALL, kind });

export interface NewSource {
  name: string;
  connector: string;
  mode: 'federated' | 'ingest';
  config: { host: string; port: number; database: string; username: string; secret_ref: string };
}

/** POST /v1/sources creates the source as draft; nothing is read before :test passes. */
export const createSource = (body: NewSource) => v1Send<SourceRow>('POST', '/sources', body);

export const updateSource = async (id: string, body: Partial<NewSource>) =>
  v1Send<SourceRow>('PATCH', `/sources/${pathId(id)}`, body);

export interface ConnectionCheck {
  key: 'network' | 'login' | 'catalog' | 'read_only' | 'version';
  title: string;
  ok: boolean;
  detail: string;
}

/** POST /v1/sources/{id}:test. PROPOSAL: the shape of the result. */
export interface ConnectionTest {
  checked_at: Timestamp;
  checks: ConnectionCheck[];
  /** Why it cannot go on and the statement that fixes it, when a check failed. */
  fix: { reason: string; sql: string } | null;
  /** Tables found, for "Chọn dữ liệu"; table_count counts the whole schema. */
  schema: string;
  table_count: number;
  tables: {
    name: string;
    description: string | null;
    personal_data: string | null;
    estimated_rows: number;
  }[];
  /** Estimates for "Sau khi lưu". */
  scan_minutes: number;
  initial_load_hours: number;
}

export const testSource = async (id: string) =>
  v1Send<ConnectionTest>('POST', `/sources/${pathId(id)}:test`);

export interface ActivateSource {
  tables: { name: string; data_space_id: string; owner_id: string }[];
  schema_scan: string;
  profiling: string;
  on_schema_change: 'pause_and_ask' | 'continue_and_notify';
}

/** POST /v1/sources/{id}:activate — rejected (409) while the read-only check fails. */
export const activateSource = async (id: string, body: ActivateSource) =>
  v1Send<SourceRow>('POST', `/sources/${pathId(id)}:activate`, body);

/* ─── Job nạp ─────────────────────────────────────────────────────────── */

export type JobStatus = 'running' | 'on_time' | 'late' | 'error' | 'paused';

/** PROPOSAL: GET /v1/ingestion-jobs ("P04 · Job nạp · Giám sát"). */
export interface IngestionJob {
  id: Uuid;
  table_name: string;
  source_name: string;
  method: 'cdc' | 'schedule' | 'incremental' | 'continuous' | 'new_files';
  /** Last 24 hours, oldest first: ok = a successful run, error = a failed one, none = no run. */
  hours: ('ok' | 'error' | 'none')[];
  lag_seconds: number | null;
  /** Lag beyond the freshness SLA. */
  late: boolean;
  rows_today: number;
  status: JobStatus;
  /** "cấu trúc đổi", "đăng nhập" — the short reason after the status. */
  status_reason: string | null;
}

export const listJobs = () => v1Get<Page<IngestionJob>>('/ingestion-jobs', ALL);

export const controlJob = async (id: string, verb: 'run' | 'pause' | 'resume') =>
  v1Send<IngestionJob>('POST', `/ingestion-jobs/${pathId(id)}:${verb}`);

/* ─── Tải tệp ─────────────────────────────────────────────────────────── */

export type UploadColumnType = 'string' | 'number' | 'percent' | 'date' | 'boolean';

export interface UploadColumn {
  header: string;
  name: string;
  type: UploadColumnType;
  unit: string | null;
  example: string;
  personal: boolean;
  /** "3 dòng sai" — rows the agent read with a guess. */
  issue: string | null;
}

/**
 * PROPOSAL: POST /v1/uploads registers a file and returns the agent's reading of it. The bytes
 * go to the presigned `upload_url` it returns; the mock skips that step.
 */
export interface UploadAnalysis {
  id: Uuid;
  file_name: string;
  sheet: string;
  row_count: number;
  header_row: number;
  columns: UploadColumn[];
  /** The sentence under the table about the rows with issues. */
  issue_note: string | null;
  target: {
    table: string;
    is_new: boolean;
    data_space_name: string;
    on_reupload: string;
    expires: string | null;
  };
}

export const createUpload = (file: { file_name: string; size_bytes: number }) =>
  v1Send<UploadAnalysis>('POST', '/uploads', file);

/** PROPOSAL: POST /v1/uploads/{id}:commit creates the table with the columns as edited. */
export const commitUpload = async (id: string, columns: UploadColumn[]) =>
  v1Send<{ table: string }>('POST', `/uploads/${pathId(id)}:commit`, { columns });

/* ─── Tài liệu: collections and documents (P05) ───────────────────────── */

export type Tone = 'success' | 'warning' | 'error' | 'info' | 'idle';

/** PROPOSAL: GET /v1/collections ("v3.1 · Phi cấu trúc · Bộ sưu tập"). */
export interface Collection {
  id: Uuid;
  name: string;
  icon: 'file' | 'mail' | 'phone';
  /** "PDF scan, ảnh · 18.400 tệp" */
  content_text: string;
  /** "14 trường · 96% đã trích · liên kết khoản vay" */
  extraction_text: string;
  /** null when archived. */
  status: { tone: Tone; text: string } | null;
  archived: boolean;
}

/** PROPOSAL: GET /v1/document-pipeline — the pipeline strip above the collection grid. */
export interface CollectionsSummary {
  ingesting_files: number;
  recognition_errors: number;
  pii_batches_pending: number;
  index_coverage: number;
}

export interface CollectionDetail extends Collection {
  document_count: number;
  /** "đồng bộ từ SharePoint “Pháp chế/Tín dụng” mỗi giờ" */
  sync_text: string;
  data_space_name: string;
  pages_pending_review: number;
}

export type StepState = 'done' | 'warn' | 'error' | 'todo';

/** One document of a collection, with its eight pre-processing steps (UC-05-02). */
export interface CollectionDocument {
  id: Uuid;
  title: string;
  /** "PDF · 48 trang" */
  type_text: string;
  steps: StepState[];
  validity: { text: string; warn: boolean } | null;
  status: { tone: Tone; text: string };
}

export interface DocumentDetail extends CollectionDocument {
  /** "Nạp tự động từ cổng văn bản · 08:12 hôm nay" */
  origin_text: string;
  processing: {
    state: 'done' | 'running' | 'pendingApproval' | 'error' | 'waiting';
    title: string;
    detail: string;
    duration: string | null;
  }[];
  excerpt: { label: string; text: string } | null;
  effective_text: string | null;
  impact_text: string | null;
}

export const listCollections = () => v1Get<Page<Collection>>('/collections', ALL);
export const getCollectionsSummary = () => v1Get<CollectionsSummary>('/document-pipeline');
export const getCollection = async (id: string) =>
  v1Get<CollectionDetail>(`/collections/${pathId(id)}`);
export const listDocuments = async (collectionId: string) =>
  v1Get<Page<CollectionDocument>>(`/collections/${pathId(collectionId)}/documents`, ALL);
export const getDocument = async (id: string) => v1Get<DocumentDetail>(`/documents/${pathId(id)}`);

/** PROPOSAL: POST /v1/collections/{id}/documents registers a file; it then runs the 8 steps. */
export const addDocument = async (
  collectionId: string,
  file: { file_name: string; size_bytes: number },
) => v1Send<CollectionDocument>('POST', `/collections/${pathId(collectionId)}/documents`, file);

/* ─── Lệch cấu trúc · Duyệt thay đổi (UC-04-03) ───────────────────────── */

/*
 * A structure change found on a connected source. It is the A2 SchemaDrift resource (P14 reads
 * the same path for crawled sources, lib/v1/crawl.ts); a connected source's drift is described
 * column by column, so everything below the id is PROPOSAL.
 */
export type ColumnChangeKind = 'add_column' | 'widen_type' | 'change_type' | 'drop_column';

export interface ColumnChange {
  column: string;
  /** The type before and after; null when the column did not exist / no longer exists. */
  before: string | null;
  after: string | null;
  kind: ColumnChangeKind;
  /** What the change means beyond its kind ("an toàn", "2 chỉ số dùng cột này"). */
  note: string | null;
}

export interface SourceDrift {
  id: Uuid;
  source_id: Uuid;
  source_name: string;
  /** The job the change paused, and its table. */
  job_id: Uuid | null;
  table_name: string;
  /** "Thẻ tín dụng đổi cấu trúc" */
  title: string;
  status: 'pending' | 'accepted' | 'rejected';
  /** "08:00 hôm nay khi quét" */
  detected_text: string;
  job_paused: boolean;
  held_records: number;
  changes: ColumnChange[];
  /** Who assessed the impact and when ("Agent Chất lượng dữ liệu", "08:02"). */
  impact_by: { name: string; at_text: string } | null;
  impact: { object: string; kind_label: string; effect: string }[];
  /** The agent's recommendation, one paragraph. */
  proposal: string | null;
  /** Who must approve, as a sentence. */
  reviewers_text: string;
  /** What happens on rejection, as a sentence. */
  if_rejected_text: string;
  /** A change breaking a metric needs a second approver (the metric's owner). */
  approvals_required: number;
  approvals: { name: string; at: Timestamp }[];
}

/** PROPOSAL: GET /v1/schema-drifts?origin=source — open drifts of connected sources. */
export const listSourceDrifts = () =>
  v1Get<Page<SourceDrift>>('/schema-drifts', { ...ALL, origin: 'source', status: 'pending' });

export const getSourceDrift = async (id: string) =>
  v1Get<SourceDrift>(`/schema-drifts/${pathId(id)}`);

/**
 * A2 :resolve. PROPOSAL: `accept` records the caller's approval and, once enough people approved,
 * resumes the job and reloads the held records; `reject` keeps the old structure.
 */
export const resolveSourceDrift = async (
  id: string,
  body: { resolution: 'accept' | 'reject'; note?: string },
) => v1Send<SourceDrift>('POST', `/schema-drifts/${pathId(id)}:resolve`, body);

/* ─── Job nạp · Thiết lập (UC-04-06) ──────────────────────────────────── */

export type JobMethod = IngestionJob['method'];

/** PROPOSAL: a table that can get an ingestion job, with what the agent suggests for it. */
export interface JobCandidate {
  dataset_id: Uuid;
  table_name: string;
  source_name: string;
  /** Columns that can serve as the incremental cursor, the suggested one first. */
  cursor_columns: { name: string; data_type: string; hint: string | null }[];
  /** The cursor's value now (the next run reads past it). */
  cursor_value: string | null;
  suggested_method: JobMethod;
  /** "raw.card_transactions · ghi thêm theo phân vùng ngày" */
  target_text: string;
  estimate: { rows_per_run: string; duration: string; source_load: string };
}

/** PROPOSAL: GET /v1/ingestion-job-candidates — tables of ingest sources without a job. */
export const listJobCandidates = () => v1Get<Page<JobCandidate>>('/ingestion-job-candidates', ALL);

export interface NewJob {
  dataset_id: Uuid;
  method: JobMethod;
  cursor_column: string | null;
  schedule: 'every_2h_daytime' | 'hourly' | 'daily_night';
  /** Lag beyond which the owner is warned. */
  freshness_sla_hours: number;
  retry: 'backoff_3' | 'once' | 'none';
  /** Always true: a run never starts while the previous one is still running. */
  no_overlap: true;
  on_schema_change: 'accept_new_columns' | 'pause_and_ask' | 'continue_and_notify';
  on_invalid_record: 'quarantine_30d' | 'skip_and_log' | 'stop';
}

export interface DryRunResult {
  rows_read: number;
  checks: { title: string; result: string; ok: boolean }[];
}

/** PROPOSAL: POST /v1/ingestion-jobs:dry-run reads 1,000 rows with the config, writes nothing. */
export const dryRunJob = (body: NewJob) =>
  v1Send<DryRunResult>('POST', '/ingestion-jobs:dry-run', body);

/** PROPOSAL: POST /v1/ingestion-jobs. Jobs run with the source's read-only account. */
export const createJob = (body: NewJob) => v1Send<IngestionJob>('POST', '/ingestion-jobs', body);

/* ─── Bản ghi lỗi (UC-04-07) ──────────────────────────────────────────── */

/** PROPOSAL: GET /v1/error-record-groups — records a job could not load, grouped by cause. */
export interface ErrorRecordGroup {
  id: Uuid;
  cause: string;
  job_id: Uuid;
  table_name: string;
  count: number;
  suggestion: string | null;
  /** Set when the records can only load after this structure change is accepted. */
  blocked_by_drift_id: Uuid | null;
  /** One record, personal data already masked, as the source sent it. */
  sample: string | null;
  retention_days: number;
}

export const listErrorGroups = () => v1Get<Page<ErrorRecordGroup>>('/error-record-groups', ALL);

/** PROPOSAL: :reload loads the groups again in time order without duplicates (409 while blocked). */
export const reloadErrorGroups = (ids: string[]) =>
  v1Send<{ reloaded: number }>('POST', '/error-record-groups:reload', { ids });

/** PROPOSAL: :discard drops the groups; the reason goes to the audit log. */
export const discardErrorGroups = (ids: string[], reason: string) =>
  v1Send<{ discarded: number }>('POST', '/error-record-groups:discard', { ids, reason });

/* ─── Khám phá bảng (UC-04-02, UC-06-02) ──────────────────────────────── */

export type FilterOp = 'eq' | 'gte' | 'lte';

export interface ExploreFilter {
  column: string;
  op: FilterOp;
  value: string;
}

export type CellValue = string | number | null;

/** PROPOSAL: POST /v1/datasets/{id}:preview — a page of rows under filters, masked. */
export interface TablePreview {
  id: Uuid;
  name: string;
  source_name: string;
  row_count: number;
  matched_rows: number;
  /** "snapshot 30/09 v3" */
  snapshot_text: string;
  columns: { name: string; type_label: string; numeric: boolean; masked: boolean }[];
  rows: CellValue[][];
  /** Indexes of rows identical to an earlier row. */
  duplicate_rows: number[];
  /** The Data Quality agent's remark on these rows, if any. */
  quality: { agent_name: string; text: string } | null;
}

export const previewTable = async (id: string, filters: ExploreFilter[]) =>
  v1Send<TablePreview>('POST', `/datasets/${pathId(id)}:preview`, { filters, limit: 50 });

/** PROPOSAL: GET /v1/datasets/{id}/columns/{name} — statistics of one column on the whole table. */
export interface ColumnStats {
  name: string;
  /** "số nguyên" */
  type_label: string;
  null_ratio: number;
  distinct: number;
  min: string;
  max: string;
  median: string;
  /** Bars 0..1, left to right; `flagged` buckets are drawn in a second colour. */
  histogram: { height: number; flagged: boolean }[];
  /** "0 · 30 · 60 · 90 · 120 · 180+ ngày" */
  buckets_text: string;
}

export const getColumnStats = async (id: string, column: string) =>
  v1Get<ColumnStats>(`/datasets/${pathId(id)}/columns/${pathId(column)}`);

/** PROPOSAL: POST /v1/datasets/{id}:derive creates a derived table from the filters. */
export const deriveTable = async (
  id: string,
  body: { filters: ExploreFilter[]; drop_duplicates: boolean },
) =>
  v1Send<{ id: Uuid; name: string; row_count: number }>(
    'POST',
    `/datasets/${pathId(id)}:derive`,
    body,
  );

/* ─── Kết nối API (UC-04-01, REST connector) ──────────────────────────── */

export type ApiAuth = 'oauth2_client' | 'api_key' | 'bearer';

/** PROPOSAL: the request a REST source makes; the secret is a reference, never the value. */
export interface ApiRequest {
  url: string;
  query: string;
  headers: { name: string; value: string }[];
  auth: ApiAuth;
  secret_ref: string;
  pagination: 'page' | 'cursor' | 'none';
  max_pages: number;
  schedule: 'hourly' | 'every_2h' | 'daily';
  rate_limit_per_second: number;
  retries: number;
}

export interface ApiFieldMapping {
  /** JSONPath-like, "data[].card_id". */
  path: string;
  column: string;
  type: 'text' | 'number' | 'date' | 'boolean';
  note: string | null;
}

/** PROPOSAL: GET /v1/sources/{id}/api-config — an existing REST source and its health. */
export interface ApiSourceConfig {
  source_id: Uuid;
  name: string;
  request: ApiRequest;
  mapping: ApiFieldMapping[];
  health: {
    last_run_text: string;
    latency_ms: number;
    errors_7d_text: string;
    /** A gap in the data, with the dates a backfill covers. */
    gap: { text: string; from: string; to: string; label: string } | null;
  } | null;
}

export const getApiConfig = async (sourceId: string) =>
  v1Get<ApiSourceConfig>(`/sources/${pathId(sourceId)}/api-config`);

interface ApiTryResult {
  status: number;
  duration_ms: number;
  /** The response body, pretty-printed, personal data masked. */
  body: string;
  /** Fields found in the response, with the agent's suggested column. */
  mapping: ApiFieldMapping[];
}

/** PROPOSAL: POST /v1/connectors/rest:try — one call with the request, nothing saved. */
export const tryApiRequest = (request: ApiRequest) =>
  v1Send<ApiTryResult>('POST', '/connectors/rest:try', { request });

/** PROPOSAL: POST /v1/sources with connector "rest" and an `api` body instead of `config`. */
export const createApiSource = (body: {
  name: string;
  request: ApiRequest;
  mapping: ApiFieldMapping[];
}) =>
  v1Send<SourceRow>('POST', '/sources', {
    name: body.name,
    connector: 'rest',
    mode: 'ingest',
    api: { request: body.request, mapping: body.mapping },
  });

/** PROPOSAL: PUT /v1/sources/{id}/api-config. */
export const saveApiConfig = async (
  sourceId: string,
  body: { request: ApiRequest; mapping: ApiFieldMapping[] },
) => v1Send<ApiSourceConfig>('PUT', `/sources/${pathId(sourceId)}/api-config`, body);

/** PROPOSAL: POST /v1/sources/{id}:backfill — load a past date range again. */
export const backfillSource = async (sourceId: string, range: { from: string; to: string }) =>
  v1Send<{ status: 'queued' }>('POST', `/sources/${pathId(sourceId)}:backfill`, range);
