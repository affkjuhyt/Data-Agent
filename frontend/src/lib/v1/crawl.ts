import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * External collection (acquire service, P14, UC-14-01…06): crawl sources, their runs, inferred
 * extraction schemas, structure drift, the review queue of extracted records, and the specs
 * that consolidate published runs into a dataset. A2 "Thu thập chủ động & hợp nhất" names the
 * endpoints; fields marked PROPOSAL are what the screens need in a shape A3 does not fix yet.
 */
export type CrawlKind = Schemas['CrawlSourceCreate']['kind'];
export type ReviewGate = 'auto' | 'exceptions' | 'all';
/** PROPOSAL: the one health state the list shows, derived from status, last run and drift. */
export type CrawlHealth =
  | 'on_schedule'
  | 'drift_quarantined'
  | 'blocked'
  | 'pending_schema'
  | 'paused';

/** GET /v1/crawl-sources — CrawlSource "kèm lần thu cuối, độ mới, tỷ lệ trích đúng, chờ duyệt". */
export interface CrawlSource {
  id: string;
  name: string;
  kind: CrawlKind;
  /** PROPOSAL: "30 miền", "sbv.gov.vn", "JSON". */
  scope_text: string;
  /** PROPOSAL: "Bảng HTML", "PDF có bảng". */
  format_label: string;
  /** PROPOSAL: schedule_cron in words. */
  schedule_text: string;
  health: CrawlHealth;
  /** PROPOSAL: "Bị chặn 403 · 3 lượt" — the health with its detail. */
  health_text: string;
  /** PROPOSAL: "08:05 · 412 bản ghi". */
  last_run_text: string | null;
  sample_accuracy_pct: number | null;
  pending_review: number;
  review_gate: ReviewGate;
  /** PROPOSAL: the open structure drift that quarantined the source, if any. */
  drift_id: string | null;
  /** PROPOSAL: the run whose records wait for review, if any. */
  review_run_id: string | null;
}

/** PROPOSAL: GET /v1/acquire/summary — the four figures over the list. */
export interface AcquireSummary {
  active: number;
  total: number;
  paused: number;
  blocked: number;
  last_agent_run_text: string;
  sample_accuracy_pct: number;
  accuracy_delta_text: string;
  sample_text: string;
  pending_records: number;
  blocking_records: number;
  latest_dataset: {
    published_at: string;
    title: string;
    detail: string;
    /** PROPOSAL: the consolidation run that is waiting to publish the next version. */
    consolidation_run_id: string | null;
  } | null;
}

type CrawlSourceWire = Schemas['CrawlSourceSummary'] & { display?: CrawlSource };

const KIND_LABEL: Record<string, string> = {
  web_page: 'Trang web',
  web_listing: 'Danh sách web',
  public_api: 'API công khai',
  feed: 'Feed',
  file_drop: 'Tệp thả vào',
};

const ddmm = (iso: string) => {
  const d = new Date(iso);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${two(d.getDate())}/${two(d.getMonth() + 1)} ${two(d.getHours())}:${two(d.getMinutes())}`;
};

/*
 * The list row from CrawlSourceSummary. The health is the first of the source's problems the
 * server reports (has_issue's inputs); scope, format and schedule in words are PROPOSAL, so the
 * row shows the domains, the kind and the cron as stored. The open drift is not linked: its
 * screen reads GET /schema-drifts/{id}, which A2 does not serve.
 */
function readCrawlSource(raw: CrawlSourceWire, reviewRunId: string | null): CrawlSource {
  if (raw.display) return raw.display;
  const blocked = raw.consecutive_blocked > 0;
  const health: CrawlHealth =
    raw.status === 'paused' || raw.status === 'retired'
      ? 'paused'
      : blocked
        ? 'blocked'
        : raw.status === 'quarantined' || (raw.open_drifts ?? 0) > 0
          ? 'drift_quarantined'
          : raw.active_schema_version_id === null
            ? 'pending_schema'
            : 'on_schedule';
  const http = raw.last_fetch_error?.http_status;
  const HEALTH_TEXT: Record<CrawlHealth, string> = {
    on_schedule: 'Đúng lịch',
    drift_quarantined: 'Đổi cấu trúc · cách ly',
    blocked: `Bị chặn${http ? ` ${http}` : ''} · ${raw.consecutive_blocked} lượt`,
    pending_schema: 'Chờ duyệt schema',
    paused: raw.status === 'retired' ? 'Đã ngừng' : 'Tạm dừng',
  };
  const domains = raw.allowed_domains;
  return {
    id: raw.id,
    name: raw.name,
    kind: raw.kind as CrawlKind,
    scope_text: domains.length > 1 ? `${domains.length} miền` : (domains[0] ?? '—'),
    format_label: KIND_LABEL[raw.kind] ?? raw.kind,
    schedule_text: raw.schedule_cron ?? 'Chạy tay',
    health,
    health_text: HEALTH_TEXT[health],
    last_run_text: raw.last_run_at ? ddmm(raw.last_run_at) : null,
    sample_accuracy_pct:
      raw.sample_accuracy === null || raw.sample_accuracy === undefined
        ? null
        : Math.round(Number(raw.sample_accuracy) * 1000) / 10,
    pending_review: raw.pending_review ?? 0,
    review_gate: raw.review_gate as ReviewGate,
    drift_id: null,
    review_run_id: reviewRunId,
  };
}

/*
 * The row links its review queue to a run; the summary has no run id, so one more read lists the
 * runs in review (newest first) and each source takes its newest.
 */
export const listCrawlSources = async (): Promise<Page<CrawlSource>> => {
  const [sources, runs] = await Promise.all([
    v1Get<Page<CrawlSourceWire>>('/crawl-sources', { page_size: 500 }),
    v1Get<Page<Schemas['CrawlRunView']>>('/crawl-runs', { status: 'in_review', page_size: 500 }),
  ]);
  const reviewRun = new Map<string, string>();
  for (const run of runs.items)
    if (!reviewRun.has(run.source_id)) reviewRun.set(run.source_id, run.id);
  return {
    ...sources,
    items: sources.items.map((s) => readCrawlSource(s, reviewRun.get(s.id) ?? null)),
  };
};

export const getAcquireSummary = () => v1Get<AcquireSummary>('/acquire/summary');

/** A2: POST /v1/crawl-sources/{id}/runs (202); 409 when the source is not active or busy. */
export const runCrawlSource = async (id: string) =>
  v1Send<{ id: string }>('POST', `/crawl-sources/${pathId(id)}/runs`);

/* ─── Đăng ký nguồn (UC-14-01) ────────────────────────────────────────── */

/**
 * PROPOSAL: POST /v1/crawl-sources:suggest {goal} — the agent proposes sources for a goal and
 * checks robots.txt and the terms of each before anything is saved (BR-149).
 */
export interface SuggestedSource {
  key: string;
  label: string;
  seed_url: string;
  domain: string;
  format_label: string;
  /** forbidden = the terms or robots.txt bar automated collection; it cannot be added. */
  robots: 'allowed' | 'forbidden';
}

export interface SourceSuggestion {
  suggested_name: string;
  sources: SuggestedSource[];
  /** PROPOSAL: the batch of sources the agent did not list one by one ("26 ngân hàng khác"). */
  more: { count: number; allowed: number; seed_urls: string[]; domains: string[] } | null;
  render_js_domains: number;
}

export const suggestCrawlSources = (goal: string) =>
  v1Send<SourceSuggestion>('POST', '/crawl-sources:suggest', { goal });

/** A2: POST /v1/crawl-sources — status draft; robots.txt checked, terms captured and hashed. */
export const createCrawlSource = (body: {
  name: string;
  goal: string;
  kind: CrawlKind;
  seed_urls: string[];
  allowed_domains: string[];
  max_depth: number;
  schedule_cron: string;
  rate_limit_rps: number;
  review_gate: ReviewGate;
  trust_level: number;
}) => v1Send<{ id: string }>('POST', '/crawl-sources', body);

/** A2: POST /v1/crawl-sources/{id}/schema-inference (202) — at least five samples. */
export const inferCrawlSchema = async (id: string) =>
  v1Send<{ id: string }>('POST', `/crawl-sources/${pathId(id)}/schema-inference`, {});

/* ─── Suy ngược schema · chạy thử (UC-14-02) ──────────────────────────── */

export interface SchemaField {
  name: string;
  type: 'text' | 'integer' | 'numeric' | 'date';
  unit: string | null;
  required: boolean;
  key: boolean;
  /** Where the value is read: a selector, a caption, a default. */
  locator: string;
  /** The canonical attribute it maps to; null when the agent is not sure. */
  canonical: string | null;
  /** PROPOSAL: the attributes a person picks from when `canonical` is null. */
  canonical_options: string[];
  /** PROPOSAL: the agent's confidence in the field; fields[] holds none. */
  confidence: number | null;
}

/** A2: GET /v1/crawl-sources/{id}/schemas — CrawlSchemaVersion, newest first. */
export interface CrawlSchemaVersion {
  id: string;
  version: number;
  status: 'draft' | 'testing' | 'active' | 'superseded' | 'rejected';
  /** PROPOSAL: "Agent Thu thập đọc 6 mẫu từ 5 ngân hàng, đề xuất 7 trường…". */
  summary_text: string;
  /** PROPOSAL: one raw sample as the agent read it, the extracted row highlighted. */
  sample: {
    url: string;
    fetched_text: string;
    columns: string[];
    rows: string[][];
    highlighted_row: number | null;
    footer_text: string;
    repeat_text: string;
  } | null;
  /** PROPOSAL: the samples read, one line each. */
  samples_read: string[];
  fields: SchemaField[];
  /**
   * The latest :test result; null before the first test run (approve then answers 409). PROPOSAL:
   * the records extracted — the version keeps only its accuracy and the pages it was tested on.
   */
  test: { pages: number; records: number | null; required_correct_pct: number } | null;
}

type SchemaWire = Schemas['CrawlSchemaVersionView'] & { display?: CrawlSchemaVersion };

const FIELD_TYPE: Record<string, SchemaField['type']> = {
  text: 'text',
  string: 'text',
  integer: 'integer',
  int: 'integer',
  number: 'numeric',
  numeric: 'numeric',
  percent: 'numeric',
  money: 'numeric',
  date: 'date',
};

/** fields is jsonb: {name, type, unit, required, is_key, locator, canonical_attr, …}. */
function readField(raw: unknown): SchemaField | null {
  if (typeof raw !== 'object' || raw === null || !('name' in raw)) return null;
  const f = raw as Record<string, unknown>;
  const text = (v: unknown) => (typeof v === 'string' && v ? v : null);
  return {
    name: String(f.name),
    type: FIELD_TYPE[String(f.type)] ?? 'text',
    unit: text(f.unit),
    required: f.required === true,
    key: f.is_key === true,
    locator: text(f.locator) ?? '—',
    canonical: text(f.canonical_attr),
    canonical_options: [],
    confidence: null,
  };
}

function readSchema(raw: SchemaWire): CrawlSchemaVersion {
  if (raw.display) return raw.display;
  const fields = raw.fields.map(readField).filter((f): f is SchemaField => f !== null);
  const accuracy = raw.test_required_accuracy;
  return {
    id: raw.id,
    version: raw.version,
    status: raw.status as CrawlSchemaVersion['status'],
    summary_text: `${fields.length} trường · vùng lặp ${raw.record_selector}`,
    sample: null,
    samples_read: [],
    fields,
    test:
      accuracy === null
        ? null
        : {
            pages: raw.sample_page_ids.length,
            records: null,
            required_correct_pct: Math.round(Number(accuracy) * 1000) / 10,
          },
  };
}

interface CrawlSourceHeader {
  id: string;
  name: string;
}

export const getCrawlSource = async (id: string) =>
  v1Get<CrawlSourceHeader>(`/crawl-sources/${pathId(id)}`);

export const listCrawlSchemas = async (sourceId: string): Promise<Page<CrawlSchemaVersion>> => {
  const page = await v1Get<Page<SchemaWire>>(`/crawl-sources/${pathId(sourceId)}/schemas`);
  return { ...page, items: page.items.map(readSchema) };
};

/** A2: :test (202) — on the held-out sample (at least 20 pages). */
export const testCrawlSchema = async (id: string) =>
  v1Send<{ id: string }>('POST', `/crawl-schemas/${pathId(id)}:test`, {});

/**
 * A2: :approve — active, previous superseded, source active (BR-151: 95% of required fields
 * right). A3 takes only a note, so the person's choice for fields the agent was unsure of is
 * written into it; PROPOSAL: `field_mappings`, which would set their canonical_attr.
 */
export const approveCrawlSchema = async (id: string, fieldMappings: Record<string, string>) => {
  const choices = Object.entries(fieldMappings).map(([field, attr]) => `${field} → ${attr}`);
  return v1Send<Schemas['CrawlSchemaVersionView']>(
    'POST',
    `/crawl-schemas/${pathId(id)}:approve`,
    choices.length ? { note: `Thuộc tính chuẩn: ${choices.join(', ')}` } : {},
  );
};

/* ─── Thay đổi cấu trúc · hoàn tác (UC-14-03) ─────────────────────────── */

/** A2: GET /v1/schema-drifts — SchemaDrift "kèm signals và phiên bản đề xuất". */
export interface SchemaDrift {
  id: string;
  source_id: string;
  source_name: string;
  /** PROPOSAL: "Thay đổi cấu trúc nặng · lượt 07/10". */
  title: string;
  /** PROPOSAL: what happened and why the run is quarantined, in one line. */
  summary_text: string;
  drift_kind: 'structural' | 'semantic';
  drift_score: number;
  thresholds: { light: number; heavy: number };
  signals: { name: string; measure: string; value: number; weight: number }[];
  /** PROPOSAL: what the drift touches, each line with a tone. */
  impact: { text: string; tone: 'warning' | 'info' | 'success' }[];
  active_version: { id: string; version: number };
  proposed_version: { id: string; version: number } | null;
  /** PROPOSAL: field by field between the version in use and the proposal. */
  changes: {
    change: 'modify' | 'add' | 'remove';
    field: string;
    before: string;
    after: string;
    test_text: string;
  }[];
  /** PROPOSAL: what reverting does, step by step (BR-152). */
  revert_steps: string[];
  resolution: 'approved_new' | 'manual_fix' | 'reverted' | 'false_alarm' | null;
}

/** PROPOSAL: GET /v1/schema-drifts/{id} (A2 only lists them). */
export const getSchemaDrift = async (id: string) =>
  v1Get<SchemaDrift>(`/schema-drifts/${pathId(id)}`);

/** A2: :resolve — closes the Cần bạn schema_drift request; the source runs again. */
export const resolveSchemaDrift = async (
  id: string,
  body: {
    resolution: 'approved_new' | 'reverted';
    proposed_version_id?: string;
    note?: string;
  },
) => v1Send<SchemaDrift>('POST', `/schema-drifts/${pathId(id)}:resolve`, body);

/* ─── Duyệt dữ liệu thu thập (UC-14-04) ───────────────────────────────── */

export type ReviewReason =
  | 'low_confidence'
  | 'cross_source_anomaly'
  | 'source_anomaly'
  | 'missing_required'
  | 'new_value'
  | 'random_sample';

export const REVIEW_REASON_LABEL: Record<ReviewReason, string> = {
  low_confidence: 'Độ tin thấp',
  cross_source_anomaly: 'Bất thường chéo nguồn',
  source_anomaly: 'Bất thường theo nguồn',
  missing_required: 'Thiếu trường bắt buộc',
  new_value: 'Giá trị mới',
  random_sample: 'Mẫu ngẫu nhiên',
};

/** A2: GET /v1/crawl-runs/{id} — CrawlRun, with what the review screen counts. PROPOSAL: the counts. */
export interface CrawlRun {
  id: string;
  source_id: string;
  source_name: string;
  /** PROPOSAL: "lượt 10/10". */
  label: string;
  status: 'extracted' | 'published';
  record_count: number;
  review_count: number;
  blocking_count: number;
  sample_count: number;
  sample_pct: number;
  /** PROPOSAL: random-sample accuracy and what it means for the review gate. */
  accuracy: {
    sample_reviewed: number;
    sample_accuracy_pct: number | null;
    /** PROPOSAL: the last four runs at 98% or more; null when the api does not say. */
    recent_runs_ok: boolean | null;
    gate: ReviewGate;
    gate_note: string;
  };
}

/** A2: GET /v1/crawl-runs/{id}/records — ExtractedRecord "kèm vùng gốc". PROPOSAL: the display shape. */
export interface ExtractedRecord {
  id: string;
  title: string;
  value_text: string;
  reasons: ReviewReason[];
  /** "Bất thường chéo nguồn", "Độ tin thấp 0,71 · PDF". */
  reason_text: string;
  /** Blocks publishing until decided (BR-153). */
  blocking: boolean;
  status: 'pending' | 'confirmed' | 'corrected' | 'rejected';
  /** The page the record was read from. */
  source_url: string | null;
  /** PROPOSAL: the region of the page around the record, its row highlighted. */
  raw: {
    caption: string;
    columns: string[];
    rows: string[][];
    highlighted_row: number | null;
    footnote: string | null;
  } | null;
  values: { field: string; value: string; confidence: number }[];
  evidence: string | null;
  agent_note: string | null;
  /** The agent's proposed correction, applied by "Xác nhận theo đề xuất". */
  proposed_values: Record<string, string> | null;
}

type RunWire = Schemas['CrawlRunDetail'] & { display?: CrawlRun };
type RecordWire = Schemas['ExtractedRecordItem'] & { display?: ExtractedRecord };

/** pending and flagged are the records a person still decides; any of them blocks :publish (BR-153). */
const UNDECIDED = new Set(['pending', 'flagged']);
const RECORD_STATUS: Record<string, ExtractedRecord['status']> = {
  pending: 'pending',
  flagged: 'pending',
  approved: 'confirmed',
  corrected: 'corrected',
  rejected: 'rejected',
  superseded: 'rejected',
};

const runRecords = (runId: string) =>
  v1Get<Page<RecordWire>>(`/crawl-runs/${pathId(runId)}/records`, { page_size: 500 });

const valueText = (v: unknown) =>
  v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);

function readRecord(raw: RecordWire): ExtractedRecord {
  if (raw.display) return raw.display;
  const values = (raw.values ?? {}) as Record<string, unknown>;
  const confidence = (raw.field_confidence ?? {}) as Record<string, unknown>;
  const fields = Object.keys(values);
  const reasons = raw.review_reasons.filter((r): r is ReviewReason => r in REVIEW_REASON_LABEL);
  return {
    id: raw.id,
    title: [raw.entity_key, raw.period].filter(Boolean).join(' · '),
    value_text: fields.length ? valueText(values[fields[0]]) : '—',
    reasons,
    reason_text: raw.review_reasons
      .map((r) => REVIEW_REASON_LABEL[r as ReviewReason] ?? r)
      .join(' · '),
    blocking: UNDECIDED.has(raw.status),
    status: RECORD_STATUS[raw.status] ?? 'pending',
    source_url: raw.source_page?.url ?? null,
    raw: null,
    values: fields.map((field) => ({
      field,
      value: valueText(values[field]),
      confidence: Number(confidence[field] ?? raw.record_confidence),
    })),
    evidence: null,
    agent_note: null,
    proposed_values: null,
  };
}

/*
 * The run as the review screen counts it. CrawlRunDetail counts extracted and flagged records
 * only, so the queue, the blocking items and the random sample are counted from the run's
 * records (one page of 500); the source's name and review gate come from the source.
 */
export const getCrawlRun = async (id: string): Promise<CrawlRun> => {
  const run = await v1Get<RunWire>(`/crawl-runs/${pathId(id)}`);
  if (run.display) return run.display;
  const [source, records] = await Promise.all([
    v1Get<Schemas['CrawlSourceDetail']>(`/crawl-sources/${pathId(run.source_id)}`),
    runRecords(run.id),
  ]);
  const undecided = records.items.filter((r) => UNDECIDED.has(r.status)).length;
  const sample = records.items.filter((r) => r.is_random_sample);
  const total = run.records_extracted;
  return {
    id: run.id,
    source_id: run.source_id,
    source_name: source.name,
    label: `lượt ${ddmm(run.started_at ?? run.created_at).slice(0, 5)}`,
    status: run.status === 'published' ? 'published' : 'extracted',
    record_count: total,
    review_count: undecided,
    blocking_count: undecided,
    sample_count: sample.length,
    sample_pct: total ? Math.round((sample.length / total) * 1000) / 10 : 0,
    accuracy: {
      sample_reviewed: sample.filter((r) => !UNDECIDED.has(r.status)).length,
      sample_accuracy_pct:
        run.sample_accuracy === null ? null : Math.round(Number(run.sample_accuracy) * 1000) / 10,
      recent_runs_ok: null,
      gate: source.review_gate as ReviewGate,
      gate_note: '',
    },
  };
};

/** The queue: the records nobody has decided yet. */
export const listRunRecords = async (runId: string): Promise<Page<ExtractedRecord>> => {
  const page = await runRecords(runId);
  return {
    ...page,
    items: page.items.filter((r) => r.display || UNDECIDED.has(r.status)).map(readRecord),
  };
};

/** A2: :review — confirm, correct (corrected_values required) or reject; corrections become examples. */
export const reviewRecord = async (
  id: string,
  body: {
    decision: 'confirm' | 'correct' | 'reject';
    corrected_values?: Record<string, string>;
    reason?: string;
  },
) => v1Send<Schemas['RecordReviewResult']>('POST', `/extracted-records/${pathId(id)}:review`, body);

/** A2: :bulk-review by filter — every pending record of the run with that reason. */
export const bulkReviewRecords = ({
  run_id,
  review_reasons,
  decision,
  reason,
}: {
  run_id: string;
  review_reasons: ReviewReason[];
  decision: 'confirm' | 'reject';
  reason: string;
}) =>
  v1Send<Schemas['BulkReviewResult']>('POST', '/extracted-records:bulk-review', {
    decision,
    filter: { run_id, review_reasons },
    reason,
  });

/** A2: :publish — 422 while blocking records remain (BR-153). */
export const publishCrawlRun = async (id: string) =>
  v1Send<Schemas['CrawlRunView']>('POST', `/crawl-runs/${pathId(id)}:publish`);

/* ─── Hợp nhất dữ liệu (UC-14-05) ─────────────────────────────────────── */

/** A2: GET /v1/consolidation-runs/{id} — ConsolidationRun. PROPOSAL: the display shape. */
export interface ConsolidationRun {
  id: string;
  spec_name: string;
  title: string;
  summary_text: string;
  status: 'pending_review' | 'published';
  next_dataset_version: number;
  steps: { label: string; state: 'done' | 'waiting' | 'pending' }[];
  table: {
    title: string;
    columns: string[];
    rows: string[][];
    highlighted_row: number | null;
    note: string | null;
  };
  /** Only the table, claims and this paragraph are embedded (BR-154). */
  summary_paragraph: { text: string; provenance: string };
  entity_matches: {
    id: string;
    alias: string;
    canonical: string;
    score: number;
    reason: string;
    decision: 'merge' | 'split' | null;
  }[];
  /** Material gaps between sources open a Cần bạn request (BR-155). */
  conflicts: { text: string; open: boolean }[];
}

export const getConsolidationRun = async (id: string) =>
  v1Get<ConsolidationRun>(`/consolidation-runs/${pathId(id)}`);

/** PROPOSAL: POST /v1/entity-matches/{id}:decide — merge the alias into the entity, or keep apart. */
export const decideEntityMatch = async (id: string, decision: 'merge' | 'split') =>
  v1Send<{ id: string; decision: string }>('POST', `/entity-matches/${pathId(id)}:decide`, {
    decision,
  });

/** PROPOSAL: :publish — the dataset version, once no entity match or conflict is open. */
export const publishConsolidationRun = async (id: string) =>
  v1Send<ConsolidationRun>('POST', `/consolidation-runs/${pathId(id)}:publish`);
