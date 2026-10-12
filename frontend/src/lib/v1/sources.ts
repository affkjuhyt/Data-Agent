import { listDataSpaces } from './api';
import { pathId, V1Error, v1Get, v1Send, v1Upload, type Page } from './client';
import type { components } from './schema';
import { readDataset, readSource, type DatasetWire, type Source, type SourceWire } from './types';

/*
 * Dữ liệu · Nguồn (P04) and Tài liệu (P05): sources, the connect flow, ingestion jobs, file
 * uploads, collections and their documents. Paths follow "Đặc tả API" (/v1/<plural>, `:verb`
 * actions, cursor pages). The spec has catalog.source and catalog.dataset; everything the
 * P04/P05 screens need beyond them is marked PROPOSAL, as in types.ts, and served by
 * mocks/sources.ts until the backend has it. The api serves the source list and writes, the
 * connection test and the datasets; without the PROPOSAL fields the rows derive what they can.
 */

type Uuid = string;
type Timestamp = string;

const ALL = { page_size: 500 };

/* ─── Nguồn: catalog.source as the P04 list draws it ──────────────────── */

export interface DataSpaceRef {
  id: Uuid;
  name: string;
}

/** PROPOSAL: the fields "P04 · Nguồn · Danh sách" shows beyond catalog.source. */
type SourceRowExtras = Omit<SourceRow, keyof Source>;

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

/** A source's row plus its tables. */
export interface SourceDetail extends SourceRow {
  tables: SourceTable[];
}

const CONNECTOR_LABEL: Record<string, string> = {
  postgresql: 'PostgreSQL',
  oracle: 'Oracle',
  sqlserver: 'SQL Server',
  mysql: 'MySQL',
  snowflake: 'Snowflake',
  bigquery: 'BigQuery',
  databricks: 'Databricks',
  clickhouse: 'ClickHouse',
  kafka_cdc: 'Kafka (CDC)',
  object_store: 'Kho đối tượng',
  file: 'Tệp',
  sftp: 'SFTP',
  sharepoint: 'SharePoint',
  rest_api: 'REST API',
  other: 'Khác',
};

const STATUS_LABEL: Record<Source['status'], string> = {
  draft: 'Nháp',
  testing: 'Đang kiểm tra',
  active: 'Đang hoạt động',
  needs_fix: 'Cần sửa',
  paused: 'Tạm dừng',
  archived: 'Đã lưu trữ',
};

/** "core-db.noibo:1521/CORE"; empty for a source without a host (files, SharePoint). */
const endpointOf = (raw: SourceWire) =>
  raw.host
    ? `${raw.host}${raw.port ? `:${raw.port}` : ''}${raw.database_name ? `/${raw.database_name}` : ''}`
    : (raw.database_name ?? '');

function readSourceRow(
  raw: SourceWire & Partial<SourceRowExtras>,
  spaces: DataSpaceRef[],
): SourceRow {
  const source = readSource(raw);
  const space = spaces.find((d) => d.id === raw.data_space_id);
  return {
    ...source,
    connector: raw.connector ?? raw.kind,
    connector_label: raw.connector_label ?? CONNECTOR_LABEL[raw.kind] ?? raw.kind,
    endpoint: raw.endpoint ?? endpointOf(raw),
    refresh_text: raw.refresh_text ?? null,
    status_label:
      raw.status_label ??
      [STATUS_LABEL[source.status], source.status_reason].filter(Boolean).join(' · '),
    schema_drift: raw.schema_drift ?? false,
    drift_id: raw.drift_id ?? null,
    lag_seconds: raw.lag_seconds ?? null,
    held_records: raw.held_records ?? null,
    data_spaces: raw.data_spaces ?? (space ? [space] : []),
  };
}

/** The Data Space names come from the spaces the viewer belongs to; a failure leaves them out. */
const spaceRefs = async (): Promise<DataSpaceRef[]> =>
  (await listDataSpaces().catch(() => null))?.items.map((d) => ({ id: d.id, name: d.name })) ?? [];

export const listSourceRows = async (): Promise<Page<SourceRow>> => {
  const [page, spaces] = await Promise.all([
    v1Get<Page<SourceWire & Partial<SourceRowExtras>>>('/sources', ALL),
    spaceRefs(),
  ]);
  return { ...page, items: page.items.map((raw) => readSourceRow(raw, spaces)) };
};

/** A table as GET /v1/datasets?source_id= gives it; counts the api lacks stay null (PROPOSAL). */
const readTable = (raw: DatasetWire & Partial<SourceTable>): SourceTable => {
  const dataset = readDataset(raw);
  return {
    id: dataset.id,
    name: raw.name ?? `${dataset.schema_name}.${dataset.table_name}`,
    description: dataset.description,
    row_count: raw.row_count ?? dataset.row_estimate,
    column_count: raw.column_count ?? null,
    refreshed_at: raw.refreshed_at ?? null,
  };
};

/** GET /v1/sources/{id} (A2 since 12/10/2026), with its tables: the datasets read from it. */
export const getSource = async (id: string): Promise<SourceDetail> => {
  const [raw, tables, spaces] = await Promise.all([
    v1Get<SourceWire & Partial<SourceRowExtras>>(`/sources/${pathId(id)}`),
    v1Get<Page<DatasetWire>>('/datasets', { ...ALL, source_id: pathId(id) }),
    spaceRefs(),
  ]);
  return { ...readSourceRow(raw, spaces), tables: tables.items.map(readTable) };
};

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

/**
 * The A3 dataset detail already contains the current columns and their latest profiles. Keep
 * the UI's table-profile shape as a client-side projection instead of calling the old proposal
 * endpoint `/datasets/{id}/profile`, which is intentionally not part of V3.
 */
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

type DatasetDetailWire = components['schemas']['DatasetDetail'] & {
  // Kept optional for richer fixtures; the canonical API derives this from `columns`.
  column_count?: number;
  profile_review?: TableProfile['review'];
};

const histogramFrom = (topValues: unknown): number[] | null => {
  if (!Array.isArray(topValues) || topValues.length === 0) return null;
  const counts = topValues.map((item) =>
    item && typeof item === 'object' && 'count' in item && typeof item.count === 'number'
      ? item.count
      : 0,
  );
  const max = Math.max(...counts);
  return max > 0 ? counts.map((count) => count / max) : null;
};

export const getTableProfile = async (id: string): Promise<TableProfile> => {
  const dataset = await v1Get<DatasetDetailWire>(`/datasets/${pathId(id)}`);
  const source = dataset.source_id
    ? await v1Get<SourceWire>(`/sources/${pathId(dataset.source_id)}`)
    : null;
  const columns = dataset.columns ?? [];
  const refreshedAt = columns
    .map((column) => column.profile?.created_at ?? null)
    .filter((value): value is string => value !== null)
    .sort()
    .at(-1) ?? null;

  return {
    id: dataset.id,
    name: dataset.display_name ?? dataset.table_name,
    source: { id: dataset.source_id ?? '', name: source?.name ?? 'Nguồn dữ liệu' },
    row_count: dataset.row_estimate ?? 0,
    column_count: dataset.column_count ?? columns.length,
    refreshed_at: refreshedAt,
    profiled_text: source?.profile_cron ?? 'Chưa lập hồ sơ',
    columns: columns.map((column) => {
      const profile = column.profile;
      return {
        name: column.name,
        data_type: column.data_type,
        null_ratio: profile?.null_ratio == null ? null : Number(profile.null_ratio),
        distinct: profile?.distinct_est ?? null,
        histogram: histogramFrom(profile?.top_values),
        histogram_note: column.pii_label ? 'che' : null,
        note: column.description,
        warn: profile?.null_ratio != null && Number(profile.null_ratio) >= 0.5,
      };
    }),
    review: dataset.profile_review ?? null,
  };
};

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

type SourceKind = components['schemas']['SourceCreate']['kind'];

/** The connector codes of the catalogue that are not catalog.source kinds already. */
const KIND: Record<string, SourceKind> = {
  rest: 'rest_api',
  kafka: 'kafka_cdc',
  folder: 'file',
  csv: 'file',
  email: 'other',
  recording: 'other',
  custom: 'other',
};

const kindOf = (connector: string): SourceKind => KIND[connector] ?? (connector as SourceKind);

/*
 * The form as SourceCreate: the database is database_name, the password is a reference to the
 * secret in OpenBao (the user name lives in that secret, so it is not sent).
 */
const sourceBody = (body: Partial<NewSource>) => ({
  name: body.name,
  ...(body.config
    ? {
        host: body.config.host || null,
        port: body.config.port || null,
        database_name: body.config.database || null,
        credentials: body.config.secret_ref ? { secret_ref: body.config.secret_ref } : null,
      }
    : {}),
});

/** POST /v1/sources creates the source as draft; nothing is read before :test passes. */
export const createSource = async (body: NewSource) =>
  readSourceRow(
    await v1Send<SourceWire>('POST', '/sources', {
      ...sourceBody(body),
      kind: kindOf(body.connector),
      mode: body.mode,
    }),
    [],
  );

/** SourcePatch keeps the kind, host and database; the name and the secret can change. */
export const updateSource = async (id: string, body: Partial<NewSource>) => {
  const { name, credentials } = sourceBody(body) as { name?: string; credentials?: unknown };
  return readSourceRow(
    await v1Send<SourceWire>('PATCH', `/sources/${pathId(id)}`, { name, credentials }),
    [],
  );
};

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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The api answers :test with 202 and writes the result back on the source (status,
 * status_reason, last_checked_at). The checks one by one, the fix and the tables found are
 * PROPOSAL: without them the result is the source's status as one check, and its tables appear
 * after the first scan.
 */
function testFromSource(row: SourceRow): ConnectionTest {
  const ok = row.status !== 'needs_fix';
  return {
    checked_at: row.last_checked_at ?? new Date().toISOString(),
    checks: [
      {
        key: 'login',
        title: 'Kết nối và đăng nhập',
        ok,
        detail: row.status_reason ?? (ok ? 'Kết nối được.' : 'Không kết nối được.'),
      },
    ],
    fix: null,
    schema: '',
    table_count: 0,
    tables: [],
    scan_minutes: 0,
    initial_load_hours: 0,
  };
}

export const testSource = async (id: string): Promise<ConnectionTest> => {
  const accepted = await v1Send<
    ConnectionTest | components['schemas']['services__api__modules__catalog__schemas__Accepted']
  >('POST', `/sources/${pathId(id)}:test`);
  if ('checks' in accepted) return accepted;
  // Poll the list until the worker has written the result, for up to about half a minute.
  for (let attempt = 0; attempt < 15; attempt++) {
    await sleep(accepted.poll_after_ms ?? 2000);
    const row = (await listSourceRows()).items.find((r) => r.id === id);
    if (row && row.status !== 'testing') return testFromSource(row);
  }
  throw new V1Error(504, 'timeout', 'Kiểm tra kết nối chưa có kết quả, hãy thử lại sau.');
};

export type ScanRun = {
  id: string;
  source_id: string;
  kind: string;
  trigger: string;
  workflow_id: string | null;
  status: string;
  tables_total: number | null;
  tables_scanned: number;
  tables_skipped: unknown;
  resume_from: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
};

export const startSourceScan = (id: string) =>
  v1Send<components['schemas']['services__api__modules__catalog__schemas__Accepted']>(
    'POST',
    `/sources/${pathId(id)}/scans`,
    { kind: 'schema' },
  );

export const getScanRun = (id: string) =>
  v1Get<ScanRun>(`/scan-runs/${pathId(id)}`);

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

interface IngestionJobWire {
  id: Uuid;
  name: string;
  mode: string;
  source_object: string;
  status: string;
  paused_reason: string | null;
  dlq_retention_days?: number;
  dlq_pending?: number;
  lag_seconds?: number | null;
  last_run?: { status?: string | null; rows_loaded?: number | null } | null;
}

const jobMethod = (mode: string): IngestionJob['method'] =>
  mode === 'cdc' ? 'cdc' : mode === 'incremental' ? 'incremental' : 'schedule';

const jobStatus = (status: string): JobStatus => {
  if (status === 'paused') return 'paused';
  if (status === 'failed' || status === 'error') return 'error';
  if (status === 'running') return 'running';
  return 'on_time';
};

function readJob(row: IngestionJobWire): IngestionJob {
  const lag = row.lag_seconds ?? null;
  const status = jobStatus(row.status);
  const runStatus = row.last_run?.status;
  const last = runStatus === 'failed' || runStatus === 'error' ? 'error' : runStatus ? 'ok' : 'none';
  return {
    id: row.id,
    table_name: row.source_object,
    source_name: row.name,
    method: jobMethod(row.mode),
    hours: [...Array(23).fill('none'), last] as IngestionJob['hours'],
    lag_seconds: lag,
    late: status === 'error' || row.status === 'late',
    rows_today: row.last_run?.rows_loaded ?? 0,
    status,
    status_reason: row.paused_reason,
  };
}

export const listJobs = async () => {
  const page = await v1Get<Page<IngestionJobWire>>('/jobs', ALL);
  return { ...page, items: page.items.map(readJob) };
};

export const controlJob = async (id: string, verb: 'run' | 'pause' | 'resume') =>
  v1Send<IngestionJobWire>('POST', `/jobs/${pathId(id)}:${verb === 'run' ? 'backfill' : verb}`).then(
    readJob,
  );

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

interface FileUploadWire {
  id: Uuid;
  file_name: string;
  size_bytes: number;
  sheet: string | null;
  header_row: number | null;
  column_map: unknown;
  error_ratio: number | string | null;
  target_dataset_id: Uuid | null;
  expires_at: Timestamp;
}

function uploadColumns(value: unknown): UploadColumn[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): UploadColumn[] => {
    if (!item || typeof item !== 'object') return [];
    const row = item as Record<string, unknown>;
    const type = ['string', 'number', 'percent', 'date', 'boolean'].includes(String(row.type))
      ? (String(row.type) as UploadColumnType)
      : 'string';
    const header = String(row.header ?? row.source ?? row.name ?? 'Cột');
    return [{
      header,
      name: String(row.name ?? row.target ?? header).toLowerCase().replace(/[^a-z0-9]+/g, '_'),
      type,
      unit: typeof row.unit === 'string' ? row.unit : null,
      example: String(row.example ?? ''),
      personal: Boolean(row.personal ?? row.is_personal),
      issue: typeof row.issue === 'string' ? row.issue : null,
    }];
  });
}

function readUpload(row: FileUploadWire): UploadAnalysis {
  const ratio = row.error_ratio === null ? 0 : Number(row.error_ratio);
  return {
    id: row.id,
    file_name: row.file_name,
    sheet: row.sheet ?? '',
    row_count: 0,
    header_row: row.header_row ?? 0,
    columns: uploadColumns(row.column_map),
    issue_note: ratio > 0 ? `Tỷ lệ dòng cần kiểm tra: ${ratio * 100}%` : null,
    target: {
      table: row.target_dataset_id ?? 'Bảng mới',
      is_new: row.target_dataset_id === null,
      data_space_name: '',
      on_reupload: 'replace',
      expires: row.expires_at,
    },
  };
}

export const createUpload = async (file: File) => {
  const form = new FormData();
  form.append('file', file);
  const row = await v1Upload<FileUploadWire>('/uploads', form, { fileName: file.name });
  return 'columns' in row && 'target' in row ? (row as unknown as UploadAnalysis) : readUpload(row);
};

/** PROPOSAL: POST /v1/uploads/{id}:commit creates the table with the columns as edited. */
export const commitUpload = async (id: string, columns: UploadColumn[]) => {
  const row = await v1Send<{ table_name?: string; display_name?: string }>(
    'POST',
    `/uploads/${pathId(id)}:commit`,
    { column_map: columns },
  );
  return { table: row.table_name ?? row.display_name ?? 'bảng mới' };
};

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

type CollectionWire = components['schemas']['CollectionView'] & { display?: Collection };

const MEDIA_LABEL: Record<string, string> = {
  pdf: 'PDF',
  image: 'ảnh',
  docx: 'Word',
  email: 'thư điện tử',
  audio: 'ghi âm',
  html: 'trang web',
};

/** A collection of mail or recordings gets their icon; files otherwise. */
function readCollection(raw: CollectionWire): Collection {
  if (raw.display) return raw.display;
  const kinds = raw.media_kinds;
  return {
    id: raw.id,
    name: raw.name,
    icon:
      kinds.length === 1 && kinds[0] === 'email'
        ? 'mail'
        : kinds.length === 1 && kinds[0] === 'audio'
          ? 'phone'
          : 'file',
    content_text: kinds.length ? kinds.map((k) => MEDIA_LABEL[k] ?? k).join(', ') : 'mọi loại tệp',
    extraction_text: raw.extraction_schema_id ? 'Có lược đồ trích' : 'Chưa có lược đồ trích',
    status:
      raw.status === 'archived'
        ? null
        : raw.status === 'reindexing'
          ? { tone: 'info', text: 'Đang lập lại chỉ mục' }
          : { tone: 'success', text: 'Đang hoạt động' },
    archived: raw.status === 'archived',
  };
}

export const listCollections = async (): Promise<Page<Collection>> => {
  const page = await v1Get<Page<CollectionWire>>('/collections', ALL);
  return { ...page, items: page.items.map(readCollection) };
};
export const getCollectionsSummary = () => v1Get<CollectionsSummary>('/document-pipeline');
export const getCollection = async (id: string) =>
  v1Get<CollectionDetail>(`/collections/${pathId(id)}`);
export const listDocuments = async (collectionId: string) =>
  v1Get<Page<CollectionDocument>>(`/collections/${pathId(collectionId)}/documents`, ALL);
type DocumentApi = components['schemas']['DocumentDetail'];

const STATUS: Record<string, { tone: Tone; text: string }> = {
  received: { tone: 'idle', text: 'Đã nhận' },
  processing: { tone: 'info', text: 'Đang xử lý' },
  waiting_review: { tone: 'warning', text: 'Chờ người xem' },
  ready: { tone: 'success', text: 'Sẵn sàng' },
  failed: { tone: 'error', text: 'Lỗi' },
  removed: { tone: 'idle', text: 'Đã gỡ' },
};

/*
 * The eight pre-processing steps (UC-05-02) said from the document's status and progress: all
 * done when ready, the review step waiting while a person must look, the first one in error
 * when it failed, and as far as the pages and chunks read while it runs.
 */
function stepsOf(raw: DocumentApi): StepState[] {
  const all = (s: StepState) => Array<StepState>(8).fill(s);
  if (raw.status === 'ready') return all('done');
  if (raw.status === 'failed') return ['error', ...Array<StepState>(7).fill('todo')];
  if (raw.status === 'received') return all('todo');
  const p = raw.progress;
  const done = p.chunks_indexed > 0 ? 6 : p.chunks > 0 ? 4 : p.pages_read > 0 ? 2 : 1;
  return Array.from(
    { length: 8 },
    (_, i): StepState =>
      i < done ? 'done' : raw.status === 'waiting_review' && i === done ? 'warn' : 'todo',
  );
}

const day = (iso: string) => iso.slice(0, 10).split('-').reverse().join('/');

/** GET /v1/documents/{id} as the collection page reads it (shared with the P05 viewer). */
export function readCollectionDocument(raw: DocumentApi): DocumentDetail {
  const ended =
    raw.effective_to !== null && raw.effective_to < new Date().toISOString().slice(0, 10);
  return {
    id: raw.id,
    title: raw.title ?? raw.file_name,
    type_text: [MEDIA_LABEL[raw.media] ?? raw.media, raw.page_count && `${raw.page_count} trang`]
      .filter(Boolean)
      .join(' · '),
    steps: stepsOf(raw),
    validity: raw.effective_to
      ? { text: `${ended ? 'Hết hiệu lực' : 'Hiệu lực tới'} ${day(raw.effective_to)}`, warn: ended }
      : raw.effective_from
        ? { text: `Từ ${day(raw.effective_from)}`, warn: false }
        : null,
    status: STATUS[raw.status] ?? { tone: 'idle', text: raw.status },
    origin_text: `Nạp ${day(raw.created_at)}`,
    processing: [],
    excerpt: null,
    effective_text: raw.effective_from ? `Hiệu lực từ ${day(raw.effective_from)}` : null,
    impact_text: null,
  };
}

type DocumentWire = DocumentApi & { display?: DocumentDetail };

export const getDocument = async (id: string) => {
  const raw = await v1Get<DocumentWire>(`/documents/${pathId(id)}`);
  return raw.display ?? readCollectionDocument(raw);
};

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
  /** How long the records stay in the dead-letter queue (DLQ), also once a batch is skipped. */
  retention_days: number;
  /** PROPOSAL: when the load batch these records came from ran ("Bỏ qua lô 08:00?"). */
  batch_at: Timestamp;
  /** PROPOSAL: the table the batch was loading into, schema-qualified ("raw.card_transactions"). */
  target_table: string;
  /** PROPOSAL: who misses data if the batch is skipped, one sentence; null when nobody reads it. */
  impact_text: string | null;
  /** IDs of the canonical ingest.dlq_record rows represented by this screen row. */
  record_ids?: Uuid[];
}

type DlqRecordWire = components['schemas']['DlqRecordView'];
const UUID_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/*
 * The V3 API exposes DLQ records per ingestion job. Keep the page-level shape local to the
 * frontend until the product needs a cross-job aggregate endpoint; calling the canonical routes
 * here avoids a second, divergent "error-record-groups" API contract.
 */
export const listErrorGroups = async (): Promise<Page<ErrorRecordGroup>> => {
  const jobs = await v1Get<Page<IngestionJobWire>>('/jobs', ALL);
  const records = (
    await Promise.all(
      jobs.items
        .filter((job) => (job.dlq_pending ?? 0) > 0)
        .map(async (job) => {
          const dlq = await v1Get<Page<DlqRecordWire>>(`/jobs/${pathId(job.id)}/dlq`, ALL);
          return dlq.items.map((record) => ({ job, record }));
        }),
    )
  ).flat();

  if (records.length === 0) {
    // Keep the existing integrated mock usable while the real API has no DLQ rows yet.
    try {
      return await v1Get<Page<ErrorRecordGroup>>('/error-record-groups', ALL);
    } catch (error) {
      if (!(error instanceof V1Error) || error.status !== 404) throw error;
    }
  }
  return {
    next_page_token: '',
    items: records.map(({ job, record }) => ({
      id: record.id,
      cause: record.error || record.error_kind,
      job_id: record.job_id,
      table_name: job.source_object,
      count: 1,
      suggestion: null,
      blocked_by_drift_id: null,
      sample: record.payload_ref,
      retention_days: job.dlq_retention_days ?? 30,
      batch_at: record.created_at,
      target_table: job.source_object,
      impact_text: null,
      record_ids: [record.id],
    })),
  };
};

/** PROPOSAL: :reload loads the groups again in time order without duplicates (409 while blocked). */
export const reloadErrorGroups = async (ids: string[]) => {
  if (!ids.every((id) => UUID_ID.test(id))) {
    return v1Send<{ reloaded: number }>('POST', '/error-record-groups:reload', { ids });
  }
  try {
    await Promise.all(ids.map((id) => v1Send('POST', `/dlq/${pathId(id)}:replay`)));
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
    return v1Send<{ reloaded: number }>('POST', '/error-record-groups:reload', { ids });
  }
  return { reloaded: ids.length };
};

/** PROPOSAL: :discard drops the groups; the reason goes to the audit log. */
export const discardErrorGroups = async (ids: string[], reason: string) => {
  if (!ids.every((id) => UUID_ID.test(id))) {
    return v1Send<{ discarded: number }>('POST', '/error-record-groups:discard', { ids, reason });
  }
  try {
    await Promise.all(
      ids.map((id) => v1Send('POST', `/dlq/${pathId(id)}:discard`, { reason })),
    );
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
    return v1Send<{ discarded: number }>('POST', '/error-record-groups:discard', { ids, reason });
  }
  return { discarded: ids.length };
};

/**
 * PROPOSAL: POST /v1/error-record-groups/{id}:skip — gives up on this batch: its records stay in
 * the DLQ for `retention_days` and never reach the target table ("Xác nhận · skipBatch" 2094:8435).
 */
export async function skipErrorGroup(id: string) {
  if (!UUID_ID.test(id)) {
    return v1Send<ErrorRecordGroup>('POST', `/error-record-groups/${pathId(id)}:skip`);
  }
  try {
    await v1Send('POST', `/dlq/${pathId(id)}:discard`, {
      reason: 'Bỏ qua lô theo yêu cầu người dùng.',
    });
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
    return v1Send<ErrorRecordGroup>('POST', `/error-record-groups/${pathId(id)}:skip`);
  }
  return { id } as ErrorRecordGroup;
}

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
/** A REST source; `api` (the request and its mapping) is PROPOSAL, the api ignores it. */
export const createApiSource = async (body: {
  name: string;
  request: ApiRequest;
  mapping: ApiFieldMapping[];
}) =>
  readSourceRow(
    await v1Send<SourceWire>('POST', '/sources', {
      name: body.name,
      kind: 'rest_api',
      mode: 'ingest',
      api: { request: body.request, mapping: body.mapping },
    }),
    [],
  );

/** PROPOSAL: PUT /v1/sources/{id}/api-config. */
export const saveApiConfig = async (
  sourceId: string,
  body: { request: ApiRequest; mapping: ApiFieldMapping[] },
) => v1Send<ApiSourceConfig>('PUT', `/sources/${pathId(sourceId)}/api-config`, body);

/** PROPOSAL: POST /v1/sources/{id}:backfill — load a past date range again. */
export const backfillSource = async (sourceId: string, range: { from: string; to: string }) =>
  v1Send<{ status: 'queued' }>('POST', `/sources/${pathId(sourceId)}:backfill`, range);
