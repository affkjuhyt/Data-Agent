import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * Data preparation (P06, UC-06-01…05): the SQL editor and its derived datasets, transformation
 * pipelines built by conversation with the Agent Chuẩn bị dữ liệu, their output table and
 * schedule, approved write-back to a source system, and lineage. A2 "Chuẩn bị dữ liệu" names the
 * resources (pipeline, derived table, write-back, lineage); fields and endpoints marked PROPOSAL
 * are what the screens need in a shape A3 does not fix yet. Data marts, data products and
 * quality rules (UC-06-06/07) live in dataProducts.ts.
 */

/* ─── SQL (v3.1 · Dữ liệu · SQL, UC-06-01 nhánh SQL) ──────────────────── */

/** PROPOSAL: GET /v1/sql/catalog — the tables the caller may query, grouped by source. */
interface SqlCatalogGroup {
  id: string;
  name: string;
  tables: string[];
}

export interface SqlResult {
  columns: string[];
  /** Cells as the warehouse returns them; masked columns already carry the mask. */
  rows: (string | number | null)[][];
  row_count: number;
  duration_ms: number;
  /** PROPOSAL: "Rủi ro tín dụng" — the Data Space whose permissions applied. */
  permission_scope: string;
  /** Columns the policy masked for this caller (BR: "cột customer_id đã che"). */
  masked_columns: string[];
}

/** PROPOSAL: the agent's one-line hint about the query, with the rewrite it proposes. */
export interface SqlSuggestion {
  text: string;
  action_label: string;
  sql: string;
}

export const getSqlCatalog = () => v1Get<{ items: SqlCatalogGroup[] }>('/sql/catalog');

/** PROPOSAL: POST /v1/sql:run — read-only, permissions of the caller, at most `limit` rows. */
export const runSql = (sql: string, limit = 500) =>
  v1Send<SqlResult>('POST', '/sql:run', { sql, limit });

/** PROPOSAL: POST /v1/sql:format — the server formats, so the editor and pipelines agree. */
export const formatSql = (sql: string) => v1Send<{ sql: string }>('POST', '/sql:format', { sql });

/** PROPOSAL: POST /v1/sql:suggest — null when the agent has nothing to add. */
export const suggestSql = (sql: string) =>
  v1Send<{ suggestion: SqlSuggestion | null }>('POST', '/sql:suggest', { sql });

/* ─── Bảng dẫn xuất (UC-06-02) ────────────────────────────────────────── */

export type WriteMode = 'replace' | 'append' | 'merge';
export type DatasetRefresh = 'after_etl' | 'hourly' | 'manual';
export type DatasetAccess = 'inherit' | 'owner_only';

/** A2: catalog.dataset row written by SQL or a pipeline; versions kept 30 (BR). */
export interface DerivedTable {
  id: string;
  /** schema.table, e.g. "prep.loans_retail". */
  name: string;
  data_space_id: string;
  write_mode: WriteMode;
  refresh: DatasetRefresh;
  access: DatasetAccess;
  version: number;
}

/** "Hộp thoại · saveDataset": POST /v1/derived-tables from the SQL in the editor. */
export const createDerivedTable = (body: {
  name: string;
  data_space_id: string;
  refresh: DatasetRefresh;
  access: DatasetAccess;
  sql: string;
}) => v1Send<DerivedTable>('POST', '/derived-tables', body);

/* ─── Pipeline (UC-06-01, UC-06-03) ───────────────────────────────────── */

/**
 * The last run's outcome when the PROPOSAL run summary says it; otherwise the pipeline's own
 * state in A2 (draft, active, paused).
 */
export type PipelineStatus =
  | 'succeeded'
  | 'failed'
  | 'running'
  | 'waiting_input'
  | 'draft'
  | 'active'
  | 'paused';

/** GET /v1/pipelines — one row of "P06 · Pipeline · Danh sách". */
export interface Pipeline {
  id: string;
  name: string;
  /** PROPOSAL: the person or agent that owns it, as shown ("Agent Chuẩn bị dữ liệu"). */
  owner_name: string;
  step_count: number;
  /** "raw.ld_loans", "crm" — the inputs as written, in order. */
  inputs: string[];
  /** The derived table it writes; null while it is a draft. */
  output_table: string | null;
  /** PROPOSAL: the schedule in words ("Sau job ld_loans", "Hằng ngày 04:00"). */
  schedule_text: string;
  last_run: { started_at: string; duration_s: number } | null;
  status: PipelineStatus;
}

export type PipelineDetailWire = components['schemas']['PipelineDetail'];
type PipelineWire = components['schemas']['PipelineView'] & { display?: Pipeline };
type DatasetRow = components['schemas']['services__api__modules__catalog__schemas__DatasetView'];

/** The schedule in words from cron, the job it follows, or the pipelines it waits for. */
const scheduleOf = (raw: components['schemas']['PipelineView']) =>
  raw.trigger_on_job_id
    ? 'Sau job nạp'
    : raw.depends_on_pipeline_ids.length
      ? `Sau ${raw.depends_on_pipeline_ids.length} pipeline`
      : raw.cron
        ? `Theo lịch ${raw.cron}`
        : 'Chạy tay';

/*
 * PipelineView names its inputs by dataset id and says nothing of the last run (PROPOSAL): the
 * inputs are named from the datasets, the status is the pipeline's own.
 */
function readPipeline(raw: PipelineWire, tables: Map<string, string>): Pipeline {
  if (raw.display) return raw.display;
  return {
    id: raw.id,
    name: raw.name,
    owner_name: '',
    step_count: Array.isArray(raw.steps) ? raw.steps.length : 0,
    inputs: raw.input_dataset_ids.map((id) => tables.get(id) ?? id),
    output_table: raw.target_dataset_id ? (tables.get(raw.target_dataset_id) ?? null) : null,
    schedule_text: scheduleOf(raw),
    last_run: null,
    status: raw.status as PipelineStatus,
  };
}

const readPipelineSteps = (raw: PipelineDetailWire): PipelineStep[] =>
  (Array.isArray(raw.steps) ? raw.steps : []).flatMap((value, index) => {
    if (typeof value !== 'object' || value === null) return [];
    const step = value as Record<string, unknown>;
    const title = typeof step.description === 'string' ? step.description : `Bước ${index + 1}`;
    const sql = typeof step.code === 'string' ? step.code : '';
    return [
      {
        seq: index + 1,
        title,
        sql,
        rows_in: 0,
        rows_out: 0,
        is_new: false,
        kind_label: typeof step.kind === 'string' ? step.kind : null,
      },
    ];
  });

/** Adapt A3's PipelineDetail to the richer presentation shape used by the pipeline screens. */
export function readPipelineDetail(
  raw: PipelineDetailWire,
  tables: Map<string, string>,
): PipelineDetail {
  // Unit/demo handlers already return the screen shape; keep them usable while the real API is
  // switched on, but adapt A3's PipelineDetail below when input_dataset_ids is present.
  if (!Array.isArray(raw.input_dataset_ids)) return raw as unknown as PipelineDetail;
  const base = readPipeline(raw, tables);
  const lastRun = raw.last_run ?? null;
  return {
    ...base,
    input_table: raw.input_dataset_ids.map((id) => tables.get(id) ?? id).join(', '),
    input_rows: lastRun?.rows_in ?? 0,
    preview_sample: lastRun?.sample_rows ?? 10_000,
    steps: readPipelineSteps(raw),
    messages: [],
  };
}

const tableName = (d: DatasetRow) => `${d.schema_name}.${d.table_name}`;

export const listPipelines = async (): Promise<Page<Pipeline>> => {
  const [page, datasets] = await Promise.all([
    v1Get<Page<PipelineWire>>('/pipelines', { page_size: 500 }),
    v1Get<Page<DatasetRow>>('/datasets', { page_size: 500 }).catch(() => null),
  ]);
  const tables = new Map<string, string>(datasets?.items.map((d) => [d.id, tableName(d)]) ?? []);
  return { ...page, items: page.items.map((p) => readPipeline(p, tables)) };
};

/**
 * A2: POST /v1/pipelines (PipelineCreate) — a draft on its input datasets; the conversation starts
 * empty. The screen names the input table ("raw.ld_loans"), so it is found among the datasets
 * first; 422 when no dataset has that name.
 */
export const createPipeline = async (body: { name: string; input_table: string }) => {
  const wanted = body.input_table.trim();
  const found = await v1Get<Page<DatasetRow>>('/datasets', {
    q: wanted.split('.').pop(),
    page_size: 50,
  });
  const dataset = found.items.find((d) => tableName(d) === wanted || d.table_name === wanted);
  if (!dataset) throw new V1Error(422, 'validation_failed', `Không tìm thấy bảng ${wanted}.`);
  const created = await v1Send<PipelineWire>('POST', '/pipelines', {
    name: body.name,
    input_dataset_ids: [dataset.id],
  } satisfies Omit<components['schemas']['PipelineCreate'], 'scope'>);
  return readPipeline(created, new Map([[dataset.id, tableName(dataset)]]));
};

/** A2: POST /v1/pipelines/{id}/runs (202); 409 while a run is going (no overlapping runs). */
export const runPipeline = async (id: string) =>
  v1Send<{ id: string }>('POST', `/pipelines/${pathId(id)}/runs`);

/* ─── Làm sạch bằng hội thoại (UC-06-01) ──────────────────────────────── */

export interface PipelineStep {
  seq: number;
  title: string;
  /** Every step is readable SQL (BR: no hidden logic). */
  sql: string;
  rows_in: number;
  rows_out: number;
  /** PROPOSAL: added by the last agent turn and not yet test-run. */
  is_new: boolean;
  /** PROPOSAL: the kind of change in one word ("lọc"), when it is not a row delta. */
  kind_label: string | null;
}

export interface PrepMessage {
  id: string;
  author: 'user' | 'agent';
  text: string;
}

/** GET /v1/pipelines/{id} — with its steps and the conversation that built them. */
export interface PipelineDetail extends Pipeline {
  input_table: string;
  input_rows: number;
  /** PROPOSAL: previews run on this many rows ("xem trước trên 1.000 dòng"). */
  preview_sample: number;
  steps: PipelineStep[];
  /** PROPOSAL: the conversation with the Agent Chuẩn bị dữ liệu (A2 keeps it on the pipeline). */
  messages: PrepMessage[];
}

/** PROPOSAL: GET /v1/pipelines/{id}/steps/{seq}/preview — one column before and after a step. */
export interface StepPreview {
  seq: number;
  key_column: string;
  column: string;
  rows: { key: string; before: string; after: string; note: string }[];
}

export const getPipeline = async (id: string): Promise<PipelineDetail> => {
  const [raw, datasets] = await Promise.all([
    v1Get<PipelineDetailWire>(`/pipelines/${pathId(id)}`),
    v1Get<Page<DatasetRow>>('/datasets', { page_size: 500 }).catch(() => null),
  ]);
  const tables = new Map<string, string>(datasets?.items.map((d) => [d.id, tableName(d)]) ?? []);
  return readPipelineDetail(raw, tables);
};

export const getStepPreview = async (id: string, seq: number) =>
  v1Get<StepPreview>(`/pipelines/${pathId(id)}/steps/${seq}/preview`);

/**
 * PROPOSAL: POST /v1/pipelines/{id}/messages — the agent answers, adds or edits steps, and
 * asks back instead of guessing on ambiguous data. Returns the pipeline as it now is.
 */
export const sendPrepMessage = async (id: string, text: string) =>
  v1Send<PipelineDetail>('POST', `/pipelines/${pathId(id)}/messages`, { text });

/** PROPOSAL: POST /v1/pipelines/{id}:test-run — every step on the full input, nothing written. */
export const testRunPipeline = async (id: string) =>
  v1Send<{ rows_out: number; excluded_rows: number; duration_s: number }>(
    'POST',
    `/pipelines/${pathId(id)}:test-run`,
  );

/* ─── Lưu thành bảng & lịch chạy (UC-06-02, UC-06-03) ─────────────────── */

export type ScheduleMode = 'after_source' | 'cron' | 'manual';

export interface Option {
  value: string;
  label: string;
}

/** PROPOSAL: GET /v1/pipelines/{id}/output-draft — what saving the pipeline would create. */
export interface OutputDraft {
  table_name: string;
  data_space_id: string;
  write_mode: WriteMode;
  columns: { name: string; type: string; from_step: number; is_new: boolean }[];
  /** PROPOSAL: who reads the table now or will ("Pipeline “Mart rủi ro tín dụng” sẽ đọc…"). */
  consumers_text: string | null;
  /** The ingestion jobs the pipeline can run after. */
  input_jobs: Option[];
  /** People and channels that can be told about a failure. */
  notify_options: Option[];
}

export interface PublishPipeline {
  table_name: string;
  data_space_id: string;
  write_mode: WriteMode;
  schedule: {
    mode: ScheduleMode;
    after_job_id: string | null;
    cron: string | null;
    retries: number;
    notify: string | null;
  };
}

export const getOutputDraft = async (id: string) =>
  v1Get<OutputDraft>(`/pipelines/${pathId(id)}/output-draft`);

/** A2: :publish — creates or versions the derived table and turns the schedule on. */
export const publishPipeline = async (id: string, body: PublishPipeline) =>
  v1Send<{ table: DerivedTable; schedule_text: string }>(
    'POST',
    `/pipelines/${pathId(id)}:publish`,
    body,
  );

/* ─── Hộp thoại · Lịch chạy (UC-06-03) ────────────────────────────────── */

export type ScheduleKind = 'daily' | 'after_source' | 'business_hours' | 'cron';

/** PROPOSAL: GET/PUT /v1/pipelines/{id}/schedule. */
export interface PipelineSchedule {
  kind: ScheduleKind;
  cron: string | null;
  skip_holidays: boolean;
  no_overlap: boolean;
  retry_and_notify: boolean;
  /** "sau job nạp Core banking" — what "Khi nguồn có dữ liệu mới" waits for. */
  after_source_label: string;
  /** "Kỹ sư dữ liệu" — who is told after the retries fail. */
  notify_label: string;
}

export const getPipelineSchedule = async (id: string) =>
  v1Get<PipelineSchedule>(`/pipelines/${pathId(id)}/schedule`);

/** PROPOSAL: the coming runs in words, from the server's business calendar. */
export const previewSchedule = async (id: string, schedule: PipelineSchedule) =>
  v1Send<{ text: string }>('POST', `/pipelines/${pathId(id)}/schedule:preview`, schedule);

export const savePipelineSchedule = async (id: string, schedule: PipelineSchedule) =>
  v1Send<{ schedule_text: string }>('PUT', `/pipelines/${pathId(id)}/schedule`, schedule);

/* ─── Ghi ngược có duyệt (UC-06-04) ───────────────────────────────────── */

export type WritebackStatus =
  | 'pending_approval'
  | 'changes_requested'
  | 'rejected'
  | 'approved'
  | 'written';

export interface Writeback {
  id: string;
  /** "WB-0042" */
  code: string;
  title: string;
  status: WritebackStatus;
  /** PROPOSAL: who proposed what, and where it goes, in one line. */
  summary_text: string;
  /** Always a staging table; Data Agent never writes the business table (BR). */
  target_table: string;
  row_count: number;
  /** A sample of the change, masked as the viewer may see it. */
  changes: { subject: string; before: string | null; after: string; reason: string }[];
  /** The dry run on the staging table. */
  checks: { ok: boolean; text: string }[];
  /** Always two, in order. */
  approvers: {
    name: string;
    role_label: string;
    state: 'pending' | 'waiting_previous' | 'approved' | 'rejected';
  }[];
  /** PROPOSAL: whether the caller is the approver whose turn it is. */
  can_decide: boolean;
  after_approval_text: string;
  rollback_text: string;
}

export type WritebackWire = components['schemas']['WritebackView'];

const objectOf = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

const textOf = (value: unknown, fallback = '') => (typeof value === 'string' ? value : fallback);

const numberOf = (value: unknown, fallback = 0) =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

const changesOf = (value: unknown): Writeback['changes'] => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const row = objectOf(item);
    if (typeof row.subject !== 'string' || typeof row.after !== 'string') return [];
    return [
      {
        subject: row.subject,
        before: typeof row.before === 'string' ? row.before : null,
        after: row.after,
        reason: textOf(row.reason),
      },
    ];
  });
};

const checksOf = (value: unknown): Writeback['checks'] => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const row = objectOf(item);
    if (typeof row.ok !== 'boolean' || typeof row.text !== 'string') return [];
    return [{ ok: row.ok, text: row.text }];
  });
};

/** Adapt A3's auditable writeback request to the richer approval screen shape. */
export function readWriteback(raw: WritebackWire): Writeback {
  // Unit/demo handlers already return the screen shape; keep them usable while the API is enabled.
  if ('code' in raw || 'title' in raw) return raw as unknown as Writeback;

  const summary = objectOf(raw.diff_summary);
  const checks = checksOf(summary.checks);
  const totals = objectOf(raw.totals_check);
  if (typeof totals.ok === 'boolean' && typeof totals.text === 'string') {
    checks.push({ ok: totals.ok, text: totals.text });
  }
  const rowCount = numberOf(summary.row_count, numberOf(summary.rows, 0));
  const status = raw.status as WritebackStatus;
  return {
    id: raw.id,
    code: raw.id,
    title: `Ghi ngược vào ${raw.target_table}`,
    status: [
      'pending_approval',
      'changes_requested',
      'rejected',
      'approved',
      'written',
    ].includes(status)
      ? status
      : 'pending_approval',
    summary_text: `Yêu cầu ghi ngược ${rowCount.toLocaleString('vi-VN')} dòng vào ${raw.target_table}.`,
    target_table: raw.target_table,
    row_count: rowCount,
    changes: changesOf(summary.changes ?? summary.sample),
    checks,
    approvers: [],
    can_decide: false,
    after_approval_text: 'Yêu cầu sẽ được xử lý theo quy trình phê duyệt của tổ chức.',
    rollback_text: raw.snapshot_ref
      ? `Có thể khôi phục từ snapshot ${raw.snapshot_ref}.`
      : 'Snapshot khôi phục chưa được cung cấp.',
  };
}

export const listOpenWritebacks = () =>
  v1Get<Page<Writeback>>('/writebacks', { status: 'pending_approval', page_size: 50 });

export const getWriteback = async (id: string) => {
  const raw = await v1Get<WritebackWire>(`/writebacks/${pathId(id)}`);
  return readWriteback(raw);
};

/** A2: :approve — the caller's step; the second approval writes to the staging table. */
export const approveWriteback = async (id: string) =>
  v1Send<Writeback>('POST', `/writebacks/${pathId(id)}:approve`, {});

export const rejectWriteback = async (id: string, reason: string) =>
  v1Send<Writeback>('POST', `/writebacks/${pathId(id)}:reject`, { reason });

export const requestWritebackChanges = async (id: string, note: string) =>
  v1Send<Writeback>('POST', `/writebacks/${pathId(id)}:request-changes`, { note });

/* ─── Dòng dữ liệu (UC-06-05) ─────────────────────────────────────────── */

export type LineageKind =
  | 'source'
  | 'raw_table'
  | 'pipeline_table'
  | 'mart'
  | 'metric'
  | 'report'
  | 'agent';

export interface LineageNode {
  id: string;
  label: string;
  kind: LineageKind;
  /** "nguồn · Oracle", "bảng thô · 10:41", "chỉ số · chứng nhận". */
  detail_text: string;
  /** Stale against its freshness SLA; edges leaving it are drawn as a warning. */
  late: boolean;
  /** PROPOSAL: what the detail panel lists when the node is selected. */
  facts: { label: string; text: string }[];
}

/**
 * PROPOSAL: GET /v1/lineage?target={id} — the target, everything it is built from, and what
 * uses it one step down. Only objects the caller may see are returned (BR).
 */
export interface LineageGraph {
  focus_id: string;
  /** "NPL_RATIO" — the heading: "<title> đi từ đâu". */
  title: string;
  nodes: LineageNode[];
  /** from → to, data flowing towards the consumer. */
  edges: { from: string; to: string }[];
}

export const getLineage = (target?: string) =>
  v1Get<LineageGraph>('/lineage', target ? { target } : undefined);

/** PROPOSAL: GET /v1/lineage/targets — the tables, metrics and reports lineage can start from. */
export const listLineageTargets = () =>
  v1Get<{ items: { id: string; label: string }[] }>('/lineage/targets');
