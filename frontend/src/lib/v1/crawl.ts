import { type Page, api, ok, okAs } from './client';
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

/**
 * The four figures over the list, said from A2's rows (GET /v1/crawl-sources, /crawl-runs,
 * /consolidation-specs and the target dataset's versions). A figure no row carries is null and
 * its line is left out.
 */
export interface AcquireSummary {
  active: number;
  total: number;
  paused: number;
  blocked: number;
  /** "Lượt thu gần nhất · 10/10 08:05"; null before any run. */
  last_agent_run_text: string | null;
  /** The mean of the sources' sample accuracy; null when no source was sampled yet. */
  sample_accuracy_pct: number | null;
  /** "trung bình 7 nguồn có mẫu"; null with no accuracy. */
  sample_text: string | null;
  pending_records: number;
  /** Records flagged in the runs that wait for review (they block publishing). */
  blocking_records: number;
  latest_dataset: {
    published_at: string;
    title: string;
    detail: string;
    /**
     * The consolidation run that made it. A2 lists neither consolidation runs nor a spec's last
     * run, so this is null against the api and the link to the run is not drawn.
     */
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
 * row shows the domains, the kind and the cron as stored.
 */
function readCrawlSource(
  raw: CrawlSourceWire,
  reviewRunId: string | null,
  driftId: string | null,
): CrawlSource {
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
    drift_id: driftId,
    review_run_id: reviewRunId,
  };
}

/*
 * The row links its review queue to a run and its open drift; the summary carries neither id, so
 * two more reads list the runs in review and the open drifts (newest first), each source taking
 * its newest.
 */
export const listCrawlSources = async (): Promise<Page<CrawlSource>> => {
  const [sources, runs, drifts] = await Promise.all([
    ok<Page<CrawlSourceWire>>(
      api.GET('/v1/crawl-sources', { params: { query: { page_size: 500 } } }),
    ),
    ok<Page<Schemas['CrawlRunView']>>(
      api.GET('/v1/crawl-runs', { params: { query: { status: 'in_review', page_size: 500 } } }),
    ),
    ok<Page<Schemas['SchemaDriftItem']>>(
      api.GET('/v1/schema-drifts', {
        params: {
          query: {
            resolution: 'open',
            page_size: 500,
          },
        },
      }),
    ),
  ]);
  const newest = (rows: { id: string; source_id: string }[]) => {
    const by = new Map<string, string>();
    for (const row of rows) if (!by.has(row.source_id)) by.set(row.source_id, row.id);
    return by;
  };
  const reviewRun = newest(runs.items);
  const openDrift = newest(drifts.items);
  return {
    ...sources,
    items: sources.items.map((s) =>
      readCrawlSource(s, reviewRun.get(s.id) ?? null, openDrift.get(s.id) ?? null),
    ),
  };
};

/** The newest version of the datasets the active consolidation specs write. */
async function latestConsolidated(): Promise<AcquireSummary['latest_dataset']> {
  const specs = await ok<Page<Schemas['ConsolidationSpecView']>>(
    api.GET('/v1/consolidation-specs', {
      params: {
        query: {
          status: 'active',
          page_size: 500,
        },
      },
    }),
  );
  const versions = await Promise.all(
    specs.items
      .filter((spec) => spec.target_dataset_id)
      .map(async (spec) => {
        const page = await ok<Page<Schemas['DatasetVersionView']>>(
          api.GET('/v1/datasets/{id}/versions', {
            params: { path: { id: spec.target_dataset_id! }, query: { page_size: 1 } },
          }),
        );
        return page.items[0] ? { spec, version: page.items[0] } : null;
      }),
  );
  const newest = versions
    .filter((v) => v !== null)
    .sort((a, b) => b.version.valid_from.localeCompare(a.version.valid_from))[0];
  return newest
    ? {
        published_at: newest.version.valid_from,
        title: newest.spec.name,
        detail: `Dataset v${newest.version.version} · ${newest.spec.source_ids.length} nguồn`,
        consolidation_run_id: null,
      }
    : null;
}

export const getAcquireSummary = async (): Promise<AcquireSummary> => {
  const [sources, runs, latest] = await Promise.all([
    listCrawlSources(),
    ok<Page<Schemas['CrawlRunView']>>(
      api.GET('/v1/crawl-runs', { params: { query: { page_size: 500 } } }),
    ),
    latestConsolidated().catch(() => null),
  ]);
  const items = sources.items;
  const paused = items.filter((s) => s.health === 'paused').length;
  const blocked = items.filter((s) => s.health === 'blocked').length;
  const sampled = items.flatMap((s) =>
    s.sample_accuracy_pct === null ? [] : [s.sample_accuracy_pct],
  );
  const lastRun = runs.items
    .flatMap((r) => (r.started_at ? [r.started_at] : []))
    .sort()
    .at(-1);
  return {
    active: items.length - paused - blocked,
    total: items.length,
    paused,
    blocked,
    last_agent_run_text: lastRun ? `Lượt thu gần nhất · ${ddmm(lastRun)}` : null,
    sample_accuracy_pct: sampled.length
      ? Math.round((sampled.reduce((n, x) => n + x, 0) / sampled.length) * 10) / 10
      : null,
    sample_text: sampled.length ? `trung bình ${sampled.length} nguồn có mẫu` : null,
    pending_records: items.reduce((n, s) => n + s.pending_review, 0),
    blocking_records: runs.items
      .filter((r) => r.status === 'in_review')
      .reduce((n, r) => n + (r.records_flagged ?? 0), 0),
    latest_dataset: latest,
  };
};

/** A2: POST /v1/crawl-sources/{id}/runs (202); 409 when the source is not active or busy. */
export const runCrawlSource = async (id: string) =>
  ok<{ id: string }>(api.POST('/v1/crawl-sources/{id}/runs', { params: { path: { id } } }));

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
  okAs<SourceSuggestion>(api.POST('/v1/crawl-sources:suggest', { body: { goal } }));

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
}) => ok<{ id: string }>(api.POST('/v1/crawl-sources', { body }));

/** A2: POST /v1/crawl-sources/{id}/schema-inference (202) — at least five samples. */
export const inferCrawlSchema = async (id: string) =>
  ok<{ id: string }>(
    api.POST('/v1/crawl-sources/{id}/schema-inference', { params: { path: { id } }, body: {} }),
  );

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
  ok<CrawlSourceHeader>(api.GET('/v1/crawl-sources/{id}', { params: { path: { id } } }));

export const listCrawlSchemas = async (sourceId: string): Promise<Page<CrawlSchemaVersion>> => {
  const page = await ok<Page<SchemaWire>>(
    api.GET('/v1/crawl-sources/{id}/schemas', { params: { path: { id: sourceId } } }),
  );
  return { ...page, items: page.items.map(readSchema) };
};

/** A2: :test (202) — on the held-out sample (at least 20 pages). */
export const testCrawlSchema = async (id: string) =>
  ok<{ id: string }>(
    api.POST('/v1/crawl-schemas/{id}:test', { params: { path: { id } }, body: {} }),
  );

/**
 * A2: :approve — active, previous superseded, source active (BR-151: 95% of required fields
 * right). A3 takes only a note, so the person's choice for fields the agent was unsure of is
 * written into it; PROPOSAL: `field_mappings`, which would set their canonical_attr.
 */
export const approveCrawlSchema = async (id: string, fieldMappings: Record<string, string>) => {
  const choices = Object.entries(fieldMappings).map(([field, attr]) => `${field} → ${attr}`);
  return ok<Schemas['CrawlSchemaVersionView']>(
    api.POST('/v1/crawl-schemas/{id}:approve', {
      params: { path: { id } },
      body: choices.length ? { note: `Thuộc tính chuẩn: ${choices.join(', ')}` } : {},
    }),
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
  /** major: a key field lost, required fields under 80% or a format change. */
  severity: 'minor' | 'major';
  /** PROPOSAL: the score and the thresholds it is held against; signals carries no fixed key. */
  drift_score: number | null;
  thresholds: { light: number; heavy: number } | null;
  /** The jsonb signals as recorded; measure and weight are PROPOSAL. */
  signals: { name: string; measure: string; value: number | string; weight: number | null }[];
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

type DriftWire = Schemas['SchemaDriftItem'] & { display?: SchemaDrift };

const DRIFT_ACTION: Record<string, string> = {
  auto_remap: 'Agent đã tự ánh xạ lại các trường',
  revert: 'Nguồn đã hoàn tác về phiên bản trước',
  paused: 'Nguồn tạm dừng đến khi có người quyết',
  quarantine: 'Lượt thu bị cách ly, chưa phát hành',
};

type FieldMap = Map<string, SchemaField>;
const fieldsOf = (raw: Schemas['CrawlSchemaVersionView'] | undefined | null): FieldMap =>
  new Map(
    (raw?.fields ?? [])
      .map(readField)
      .filter((f): f is SchemaField => f !== null)
      .map((f) => [f.name, f]),
  );
const fieldText = (f: SchemaField) =>
  [f.type, f.unit, f.locator !== '—' ? f.locator : null].filter(Boolean).join(' · ');

/** Field by field between the version in use and the proposal, from both versions' fields. */
function diffFields(before: FieldMap, after: FieldMap): SchemaDrift['changes'] {
  const changes: SchemaDrift['changes'] = [];
  for (const [name, f] of before) {
    const next = after.get(name);
    if (!next)
      changes.push({
        change: 'remove',
        field: name,
        before: fieldText(f),
        after: '—',
        test_text: '',
      });
    else if (fieldText(next) !== fieldText(f))
      changes.push({
        change: 'modify',
        field: name,
        before: fieldText(f),
        after: fieldText(next),
        test_text: '',
      });
  }
  for (const [name, f] of after)
    if (!before.has(name))
      changes.push({ change: 'add', field: name, before: '—', after: fieldText(f), test_text: '' });
  return changes;
}

/*
 * One drift as its screen reads it. A2 only lists drifts, so it is found in the list; its source's
 * name and the version numbers come from the source and its schemas. Title and summary say what
 * the server recorded (kind, severity, the action taken); the impact and the revert steps are
 * PROPOSAL, left empty.
 */
export const getSchemaDrift = async (id: string): Promise<SchemaDrift> => {
  const page = await ok<Page<DriftWire>>(
    api.GET('/v1/schema-drifts', { params: { query: { page_size: 500 } } }),
  );
  const raw = page.items.find((d) => d.id === id);
  if (!raw) throw new Error('Không tìm thấy sự cố đổi cấu trúc.');
  if (raw.display) return raw.display;
  const [source, schemas] = await Promise.all([
    ok<Schemas['CrawlSourceDetail']>(
      api.GET('/v1/crawl-sources/{id}', { params: { path: { id: raw.source_id } } }),
    ),
    ok<Page<Schemas['CrawlSchemaVersionView']>>(
      api.GET('/v1/crawl-sources/{id}/schemas', {
        params: { path: { id: raw.source_id }, query: { page_size: 500 } },
      }),
    ),
  ]);
  const from = schemas.items.find((v) => v.id === raw.from_version_id);
  const proposed =
    raw.proposed_version ?? schemas.items.find((v) => v.id === raw.proposed_version_id) ?? null;
  const signals = Object.entries((raw.signals ?? {}) as Record<string, unknown>).flatMap(
    ([name, value]) =>
      typeof value === 'number' || typeof value === 'string'
        ? [{ name, measure: '', value, weight: null }]
        : [],
  );
  const semantic = raw.drift_kind === 'semantic';
  const major = raw.severity === 'major';
  return {
    id: raw.id,
    source_id: raw.source_id,
    source_name: source.name,
    title: `${semantic ? 'Lệch ngầm' : 'Thay đổi cấu trúc'} ${major ? 'nặng' : 'nhẹ'} · lượt ${ddmm(raw.created_at).slice(0, 5)}`,
    summary_text: DRIFT_ACTION[raw.action] ?? raw.action,
    drift_kind: raw.drift_kind as SchemaDrift['drift_kind'],
    severity: raw.severity as SchemaDrift['severity'],
    drift_score: null,
    thresholds: null,
    signals,
    impact: [],
    active_version: { id: raw.from_version_id, version: from?.version ?? 0 },
    proposed_version: proposed ? { id: proposed.id, version: proposed.version } : null,
    changes: proposed ? diffFields(fieldsOf(from), fieldsOf(proposed)) : [],
    revert_steps: [],
    resolution: (raw.resolution ?? null) as SchemaDrift['resolution'],
  };
};

/** A2: :resolve — closes the Cần bạn schema_drift request; the source runs again. */
export const resolveSchemaDrift = async (
  id: string,
  body: {
    resolution: 'approved_new' | 'reverted';
    proposed_version_id?: string;
    note?: string;
  },
) =>
  ok<Schemas['SchemaDriftItem']>(
    api.POST('/v1/schema-drifts/{id}:resolve', { params: { path: { id } }, body }),
  );

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
  ok<Page<RecordWire>>(
    api.GET('/v1/crawl-runs/{id}/records', {
      params: { path: { id: runId }, query: { page_size: 500 } },
    }),
  );

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
  const run = await ok<RunWire>(api.GET('/v1/crawl-runs/{id}', { params: { path: { id } } }));
  if (run.display) return run.display;
  const [source, records] = await Promise.all([
    ok<Schemas['CrawlSourceDetail']>(
      api.GET('/v1/crawl-sources/{id}', { params: { path: { id: run.source_id } } }),
    ),
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
) =>
  ok<Schemas['RecordReviewResult']>(
    api.POST('/v1/extracted-records/{id}:review', { params: { path: { id } }, body }),
  );

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
  ok<Schemas['BulkReviewResult']>(
    api.POST('/v1/extracted-records:bulk-review', {
      body: {
        decision,
        filter: { run_id, review_reasons },
        reason,
      },
    }),
  );

/** A2: :publish — 422 while blocking records remain (BR-153). */
export const publishCrawlRun = async (id: string) =>
  ok<Schemas['CrawlRunView']>(
    api.POST('/v1/crawl-runs/{id}:publish', { params: { path: { id } } }),
  );

/* ─── Hợp nhất dữ liệu (UC-14-05) ─────────────────────────────────────── */

/** A2: GET /v1/consolidation-runs/{id} — ConsolidationRun. PROPOSAL: the display shape. */
export interface ConsolidationRun {
  id: string;
  spec_name: string;
  title: string;
  summary_text: string;
  status: 'pending_review' | 'published' | 'failed';
  /**
   * The dataset version the run wrote: A3's run publishes it itself, and the person's gate is
   * certifying it (BR-156). A2's run carries only the version's id; its number is PROPOSAL.
   */
  dataset_version_id: string | null;
  dataset_version: number | null;
  /** PROPOSAL: the steps the run went through. */
  steps: { label: string; state: 'done' | 'waiting' | 'pending' }[];
  /** PROPOSAL: the consolidated table. */
  table: {
    title: string;
    columns: string[];
    rows: string[][];
    highlighted_row: number | null;
    note: string | null;
  } | null;
  /** Only the table, claims and this paragraph are embedded (BR-154). PROPOSAL. */
  summary_paragraph: { text: string; provenance: string } | null;
  /** A2 EntityLink, pending for the spec's entity type and sources. */
  entity_matches: {
    id: string;
    alias: string;
    canonical: string;
    score: number | null;
    reason: string;
    decision: 'merge' | 'split' | null;
  }[];
  /** Material gaps between sources open a Cần bạn request (BR-155). */
  conflicts: { text: string; open: boolean }[];
}

type ConsolidationWire = Schemas['ConsolidationRunView'] & { display?: ConsolidationRun };

const LINK_METHOD: Record<string, string> = {
  identifier: 'khớp mã định danh',
  exact: 'khớp tên chính xác',
  // BR-169: a fuzzy match under 0.95 waits for a person.
  fuzzy: 'khớp gần đúng',
  human: 'người quyết',
};

/*
 * The run with what the api records of it (counts in and out, claims, embedded chunks, conflicts
 * opened) and the entity links still waiting for its spec's entity type among its sources. The
 * conflicts themselves are in Xung đột; the run only counts them.
 */
export const getConsolidationRun = async (id: string): Promise<ConsolidationRun> => {
  const run = await ok<ConsolidationWire>(
    api.GET('/v1/consolidation-runs/{id}', { params: { path: { id } } }),
  );
  if (run.display) return run.display;
  const specs = await ok<Page<Schemas['ConsolidationSpecView']>>(
    api.GET('/v1/consolidation-specs', {
      params: {
        query: {
          page_size: 500,
        },
      },
    }),
  );
  const spec = specs.items.find((s) => s.id === run.spec_id);
  const links = spec
    ? await ok<Page<Schemas['EntityLinkView']>>(
        api.GET('/v1/entity-links', {
          params: {
            query: {
              entity_type: spec.entity_type,
              status: 'pending',
              page_size: 500,
            },
          },
        }),
      )
    : { items: [] };
  const sources = new Set(spec?.source_ids ?? []);
  const name = spec?.name ?? 'Đặc tả hợp nhất';
  return {
    id: run.id,
    spec_name: name,
    title: `Hợp nhất ${name} · ${ddmm(run.started_at)}`,
    summary_text: [
      `${run.records_in} bản ghi vào, ${run.records_out} ra`,
      `${run.claims_created} khẳng định`,
      `${run.chunks_embedded} đoạn nhúng`,
    ].join(' · '),
    status:
      run.status === 'published'
        ? 'published'
        : run.status === 'failed'
          ? 'failed'
          : 'pending_review',
    dataset_version_id: run.dataset_version_id,
    dataset_version: null,
    steps: [],
    table: null,
    summary_paragraph: null,
    entity_matches: links.items
      .filter((l) => sources.has(l.source_id))
      .map((l) => ({
        id: l.id,
        alias: l.source_key,
        canonical: l.canonical_key,
        score: l.match_score === null ? null : Number(l.match_score),
        reason: LINK_METHOD[l.method] ?? l.method,
        decision: null,
      })),
    conflicts: run.conflicts_opened
      ? [{ text: `${run.conflicts_opened} xung đột giữa nguồn mở ở Xung đột (BR-155)`, open: true }]
      : [],
  };
};

/** A2: POST /v1/entity-links/{id}:decide — merge confirms the link, keeping apart rejects it. */
export const decideEntityMatch = async (id: string, decision: 'merge' | 'split') =>
  ok<Schemas['EntityLinkView']>(
    api.POST('/v1/entity-links/{id}:decide', {
      params: { path: { id } },
      body: {
        status: decision === 'merge' ? 'confirmed' : 'rejected',
      },
    }),
  );

/** A2: POST /v1/dataset-versions/{id}:certify — the person certifies the version the run wrote. */
export const certifyDatasetVersion = async (datasetVersionId: string) =>
  ok<Schemas['CertifiedVersion']>(
    api.POST('/v1/dataset-versions/{id}:certify', {
      params: { path: { id: datasetVersionId } },
      body: {},
    }),
  );
