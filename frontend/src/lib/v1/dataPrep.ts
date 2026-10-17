import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';
import { awaitQueryRun, getQueryRows } from './explore';

/*
 * Data preparation (P06, UC-06-01…05): the SQL editor and its derived datasets, transformation
 * pipelines built by conversation with the Agent Chuẩn bị dữ liệu, their output table and
 * schedule, approved write-back to a source system, and lineage. A2 "Chuẩn bị dữ liệu" names the
 * resources (pipeline, derived table, write-back, lineage); fields and endpoints marked PROPOSAL
 * are what the screens need in a shape A3 does not fix yet. Data marts, data products and
 * quality rules (UC-06-06/07) live in dataProducts.ts.
 */

/* ─── SQL (v3.1 · Dữ liệu · SQL, UC-06-01 nhánh SQL) ──────────────────── */

/** The tables the caller may query, grouped from the canonical dataset catalogue. */
interface SqlCatalogGroup {
  id: string;
  name: string;
  tables: SqlCatalogTable[];
}

export interface SqlCatalogTable {
  id: string;
  name: string;
  columns: { name: string; type: string | null }[];
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

export const getSqlCatalog = async (): Promise<{ items: SqlCatalogGroup[] }> => {
  const datasets = await v1Get<Page<DatasetRow>>('/datasets', { page_size: 500 });
  const details = await Promise.all(
    datasets.items.map(async (dataset) => {
      const detail = await v1Get<components['schemas']['DatasetDetail']>(
        `/datasets/${pathId(dataset.id)}`,
      ).catch(() => null);
      return { dataset, detail };
    }),
  );
  const sourceNames = new Map<string, string>();
  await Promise.all(
    [...new Set(datasets.items.map((dataset) => dataset.source_id).filter(Boolean))].map(
      async (sourceId) => {
        const source = await v1Get<{ id: string; name: string }>(
          `/sources/${pathId(sourceId!)}`,
        ).catch(() => null);
        if (source) sourceNames.set(source.id, source.name);
      },
    ),
  );
  const groups = new Map<string, SqlCatalogGroup>();
  for (const { dataset, detail } of details) {
    const key = dataset.source_id ?? 'derived';
    const group = groups.get(key) ?? {
      id: key,
      name: dataset.source_id ? (sourceNames.get(key) ?? 'Nguồn dữ liệu') : 'Bảng dẫn xuất',
      tables: [],
    };
    group.tables.push({
      id: dataset.id,
      name: tableName(dataset),
      columns: (detail?.columns ?? []).map((column) => ({
        name: column.name,
        type: column.data_type,
      })),
    });
    groups.set(key, group);
  }
  return { items: [...groups.values()] };
};

/** Read-only SQL through the canonical query-run worker and policy path. */
export const runSql = async (sql: string, limit = 500): Promise<SqlResult> => {
  const run = await v1Send<components['schemas']['QueryRunView']>('POST', '/query-runs', {
    sql,
    limit,
  });
  const finished = await awaitQueryRun(run);
  const rows = await getQueryRows(finished.id, limit);
  const policy = finished as typeof finished & {
    permission_scope?: string;
    masked_columns?: string[];
  };
  return {
    columns: rows.columns.map((column) => column.name),
    rows: rows.rows as SqlResult['rows'],
    row_count: finished.row_count ?? rows.rows.length,
    duration_ms: finished.duration_ms ?? 0,
    permission_scope: policy.permission_scope ?? 'Data Space hiện tại',
    masked_columns: policy.masked_columns ?? [],
  };
};

/** Server formatting when available; otherwise keep a deterministic read-only fallback. */
export const formatSql = async (sql: string): Promise<{ sql: string }> => {
  return { sql: sql.trim().replace(/\s+/g, ' ') };
};

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

type PipelineDisplayWire = Partial<Pipeline> & {
  input_table?: string;
  input_rows?: number;
  preview_sample?: number;
  steps?: PipelineStep[];
  messages?: PrepMessage[];
};
export type PipelineDetailWire = components['schemas']['PipelineDetail'] & {
  display?: PipelineDisplayWire;
  messages?: PrepMessage[];
};
type PipelineWire = components['schemas']['PipelineView'] & { display?: PipelineDisplayWire };
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
  const base: Pipeline = {
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
  return raw.display ? { ...base, ...raw.display, id: raw.id } : base;
}

const readPipelineSteps = (raw: PipelineDetailWire): PipelineStep[] =>
  (Array.isArray(raw.steps) ? raw.steps : []).flatMap((value, index) => {
    if (typeof value !== 'object' || value === null) return [];
    const step = value as Record<string, unknown>;
    const title = typeof step.description === 'string' ? step.description : `Bước ${index + 1}`;
    const sql = typeof step.code === 'string' ? step.code : '';
    const rowsIn = typeof step.rows_in === 'number' ? step.rows_in : 0;
    const rowsOut = typeof step.rows_out === 'number' ? step.rows_out : 0;
    return [
      {
        seq: index + 1,
        title,
        sql,
        rows_in: rowsIn,
        rows_out: rowsOut,
        is_new: step.is_new === true,
        kind_label:
          typeof step.kind_label === 'string'
            ? step.kind_label
            : typeof step.kind === 'string'
              ? step.kind
              : null,
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
  const display = raw.display;
  if (display) {
    return {
      ...readPipeline({ ...raw, ...display } as PipelineWire, tables),
      input_table: display.input_table ?? '',
      input_rows: display.input_rows ?? 0,
      preview_sample: display.preview_sample ?? 10_000,
      steps: display.steps ?? readPipelineSteps(raw),
      messages: display.messages ?? raw.messages ?? [],
    };
  }
  const base = readPipeline(raw, tables);
  const lastRun = raw.last_run ?? null;
  return {
    ...base,
    input_table: raw.input_dataset_ids.map((id) => tables.get(id) ?? id).join(', '),
    input_rows: lastRun?.rows_in ?? 0,
    preview_sample: lastRun?.sample_rows ?? 10_000,
    steps: readPipelineSteps(raw),
    messages: raw.messages ?? [],
  };
}

const tableName = (d: DatasetRow) =>
  d.schema_name ? `${d.schema_name}.${d.table_name}` : d.table_name;

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
export const testRunPipeline = async (id: string) => {
  try {
    return await v1Send<{ rows_out: number; excluded_rows: number; duration_s: number }>(
      'POST',
      `/pipelines/${pathId(id)}:test-run`,
    );
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
    const run = await v1Send<components['schemas']['PipelineRunView']>(
      'POST',
      `/pipelines/${pathId(id)}:preview`,
      { sample_rows: null },
    );
    const started = new Date(run.started_at).getTime();
    const finished = run.finished_at ? new Date(run.finished_at).getTime() : started;
    const dropped =
      typeof run.rows_dropped === 'object' && run.rows_dropped !== null
        ? (run.rows_dropped as { total?: unknown }).total
        : 0;
    return {
      rows_out: run.rows_out ?? 0,
      excluded_rows: typeof dropped === 'number' ? dropped : 0,
      duration_s: Math.max(0, finished - started) / 1000,
    };
  }
};

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

/** PUT /v1/pipelines/{id}/schedule; GET remains a frontend compatibility projection. */
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
  /** Preserved API id for an existing source-triggered schedule. */
  after_source_job_id?: string | null;
}

const cronForSchedule = (schedule: PipelineSchedule) =>
  schedule.kind === 'daily'
    ? '30 6 * * *'
    : schedule.kind === 'business_hours'
      ? '0 8-17 * * 1-5'
      : schedule.kind === 'cron'
        ? schedule.cron
        : null;

export const getPipelineSchedule = async (id: string) =>
  v1Get<components['schemas']['ScheduleRequest'] & Partial<PipelineSchedule>>(
    `/pipelines/${pathId(id)}/schedule`,
  ).then((schedule) => {
    const kind =
      schedule.kind ??
      (schedule.cron ? 'cron' : schedule.trigger_on_job_id ? 'after_source' : 'daily');
    return {
      kind: kind as ScheduleKind,
      cron: schedule.cron ?? null,
      skip_holidays: false,
      no_overlap: schedule.no_overlap ?? true,
      retry_and_notify:
        schedule.on_failure === 'retry_then_notify' || schedule.retry_and_notify === true,
      after_source_label:
        schedule.after_source_label ?? schedule.trigger_on_job_id ?? 'job nạp dữ liệu',
      notify_label:
        schedule.notify_label ??
        (schedule.on_failure === 'skip' ? 'Không thông báo' : 'Người phụ trách pipeline'),
      after_source_job_id: schedule.trigger_on_job_id ?? null,
    };
  });

/** PROPOSAL: the coming runs in words, from the server's business calendar. */
export const previewSchedule = async (id: string, schedule: PipelineSchedule) =>
  v1Send<{ text: string }>('POST', `/pipelines/${pathId(id)}/schedule:preview`, {
    cron: cronForSchedule(schedule),
    no_overlap: schedule.no_overlap,
    on_failure: schedule.retry_and_notify ? 'retry_then_notify' : 'notify',
    schedule_kind: schedule.kind,
  });

export const savePipelineSchedule = async (id: string, schedule: PipelineSchedule) =>
  v1Send<components['schemas']['PipelineView'] & { schedule_text?: string }>(
    'PUT',
    `/pipelines/${pathId(id)}/schedule`,
    {
      cron: cronForSchedule(schedule),
      work_calendar_id: null,
      trigger_on_job_id:
        schedule.kind === 'after_source' ? (schedule.after_source_job_id ?? null) : null,
      depends_on_pipeline_ids: [],
      no_overlap: schedule.no_overlap,
      on_failure: schedule.retry_and_notify ? 'retry_then_notify' : 'notify',
    },
  ).then((view) => ({
    schedule_text:
      schedule.kind === 'daily'
        ? 'chạy hằng ngày lúc 06:30'
        : schedule.kind === 'business_hours'
          ? 'chạy mỗi giờ trong giờ làm việc'
          : schedule.kind === 'after_source'
            ? 'chạy sau job nạp Core banking'
            : (view.schedule_text ?? (view.cron ? `Theo lịch cron ${view.cron}` : 'Chạy thủ công')),
  }));

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
  proposal_id?: string | null;
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
  const rawStatus = String(raw.status);
  const status = rawStatus as WritebackStatus;
  const pending = rawStatus === 'submitted' || rawStatus === 'pending_approval';
  return {
    id: raw.id,
    code: raw.id,
    title: `Ghi ngược vào ${raw.target_table}`,
    status: ['pending_approval', 'changes_requested', 'rejected', 'approved', 'written'].includes(
      status,
    )
      ? rawStatus === 'submitted'
        ? 'pending_approval'
        : status
      : 'pending_approval',
    summary_text: `Yêu cầu ghi ngược ${rowCount.toLocaleString('vi-VN')} dòng vào ${raw.target_table}.`,
    target_table: raw.target_table,
    row_count: rowCount,
    changes: changesOf(summary.changes ?? summary.sample),
    checks,
    approvers: pending
      ? [
          {
            name: 'Người duyệt theo chính sách',
            role_label: 'Phê duyệt ghi ngược trong Cần bạn',
            state: 'pending',
          },
        ]
      : [],
    can_decide: pending && Boolean(raw.proposal_id),
    after_approval_text: 'Yêu cầu sẽ được xử lý theo quy trình phê duyệt của tổ chức.',
    rollback_text: raw.snapshot_ref
      ? `Có thể khôi phục từ snapshot ${raw.snapshot_ref}.`
      : 'Snapshot khôi phục chưa được cung cấp.',
  };
}

export const listOpenWritebacks = () =>
  v1Get<Page<WritebackWire>>('/writebacks', { status: 'submitted', page_size: 50 }).then(
    (page) => ({
      ...page,
      items: page.items.map(readWriteback),
    }),
  );

export const getWriteback = async (id: string) => {
  const raw = await v1Get<WritebackWire>(`/writebacks/${pathId(id)}`);
  return readWriteback(raw);
};

/** A2: :approve — the caller's step; the second approval writes to the staging table. */
export const approveWriteback = async (id: string) => decideWriteback(id, { decision: 'accept' });

export const rejectWriteback = async (id: string, reason: string) =>
  decideWriteback(id, { decision: 'reject', note: reason });

export const requestWritebackChanges = async (id: string, note: string) =>
  decideWriteback(id, { decision: 'request_changes', note });

type WritebackDecision =
  | { decision: 'accept' }
  | { decision: 'reject'; note: string }
  | { decision: 'request_changes'; note: string };

async function decideWriteback(id: string, body: WritebackDecision): Promise<Writeback> {
  const current = await getWriteback(id);
  if (!current.proposal_id)
    throw new Error('Lệnh ghi ngược chưa có proposal phê duyệt từ hệ thống.');
  await v1Send('POST', `/proposals/${pathId(current.proposal_id)}:decide`, body);
  return getWriteback(id);
}

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
 * GET /v1/lineage — the target, everything it is built from, and what
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

/** GET /v1/lineage as the api sends it (pipeline/schemas.py LineageGraph). */
interface LineageWire {
  root: LineageNodeWire;
  nodes: LineageNodeWire[];
  edges: { source: string; target: string; via: string | null; via_ref: string | null }[];
  grouped: boolean;
  groups: { kind: string; count: number }[];
}
interface LineageNodeWire {
  key: string;
  kind: string;
  id: string | null;
  name: string | null;
  hidden: boolean;
}

const LINEAGE_KIND: Record<string, LineageKind> = {
  source: 'source',
  dataset: 'raw_table',
  column: 'raw_table',
  data_product: 'mart',
  metric: 'metric',
  tile: 'report',
  report: 'report',
  skill: 'agent',
  agent: 'agent',
};
const KIND_TEXT: Record<string, string> = {
  source: 'nguồn',
  dataset: 'bảng',
  column: 'cột',
  data_product: 'sản phẩm dữ liệu',
  metric: 'chỉ số',
  tile: 'ô phiên',
  report: 'báo cáo',
  skill: 'kỹ năng',
  agent: 'agent',
};
const VIA_TEXT: Record<string, string> = {
  catalog: 'đọc từ nguồn',
  pipeline: 'pipeline',
  semantic: 'lớp ngữ nghĩa',
  query: 'truy vấn',
};

function readLineage(raw: LineageWire): LineageGraph {
  const into = new Map<string, string[]>();
  for (const e of raw.edges) {
    if (e.via) into.set(e.target, [...(into.get(e.target) ?? []), VIA_TEXT[e.via] ?? e.via]);
  }
  return {
    focus_id: raw.root.key,
    title: raw.root.name ?? 'Đối tượng',
    nodes: raw.nodes.map((n) => ({
      id: n.key,
      label: n.name ?? KIND_TEXT[n.kind] ?? n.kind,
      kind: LINEAGE_KIND[n.kind] ?? 'raw_table',
      detail_text: KIND_TEXT[n.kind] ?? n.kind,
      // No freshness is tracked per node yet: nothing is drawn as late rather than guessed.
      late: false,
      facts: [
        { label: 'Loại', text: KIND_TEXT[n.kind] ?? n.kind },
        ...(into.get(n.key)?.length
          ? [{ label: 'Tạo ra qua', text: [...new Set(into.get(n.key))].join(', ') }]
          : []),
      ],
    })),
    edges: raw.edges.map((e) => ({ from: e.source, to: e.target })),
  };
}

/** The objects a lineage graph can start from (datasets of the Data Space). */
export const listLineageTargets = async () => ({
  items: await v1Get<{ id: string; label: string }[]>('/lineage/targets'),
});

/**
 * The graph of `target` (a dataset id), both ways: what it is built from and what uses it.
 * Without a target, the first dataset of the Data Space; with none, an empty graph.
 */
export const getLineage = async (target?: string): Promise<LineageGraph> => {
  const id = target ?? (await listLineageTargets()).items[0]?.id;
  if (!id) return { focus_id: '', title: 'Chưa có bảng nào', nodes: [], edges: [] };
  const raw = await v1Get<LineageWire>('/lineage', {
    object_kind: 'dataset',
    object_id: id,
    direction: 'both',
  });
  return readLineage(raw);
};
