import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * External collection (acquire service, P14, UC-14-01…06): crawl sources, their runs, inferred
 * extraction schemas, structure drift, the review queue of extracted records, and the specs
 * that consolidate published runs into a dataset. A2 "Thu thập chủ động & hợp nhất" names the
 * endpoints; fields marked PROPOSAL are what the screens need in a shape A3 does not fix yet.
 */
export type CrawlKind = 'web_listing' | 'web_page' | 'public_api';
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
  latest_dataset: { published_at: string; title: string; detail: string } | null;
}

export const listCrawlSources = () =>
  v1Get<Page<CrawlSource>>('/crawl-sources', { page_size: 500 });

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
  confidence: number;
}

/** A2: GET /v1/crawl-sources/{id}/schemas — CrawlSchemaVersion, newest first. */
export interface CrawlSchemaVersion {
  id: string;
  version: number;
  status: 'draft' | 'active' | 'superseded';
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
  };
  /** PROPOSAL: the samples read, one line each. */
  samples_read: string[];
  fields: SchemaField[];
  /** The latest :test result; null before the first test run (approve then answers 409). */
  test: { pages: number; records: number; required_correct_pct: number } | null;
}

export interface CrawlSourceHeader {
  id: string;
  name: string;
}

export const getCrawlSource = async (id: string) =>
  v1Get<CrawlSourceHeader>(`/crawl-sources/${pathId(id)}`);

export const listCrawlSchemas = async (sourceId: string) =>
  v1Get<Page<CrawlSchemaVersion>>(`/crawl-sources/${pathId(sourceId)}/schemas`);

/** A2: :test (202) — on the held-out sample (at least 20 pages). */
export const testCrawlSchema = async (id: string) =>
  v1Send<{ id: string }>('POST', `/crawl-schemas/${pathId(id)}:test`, {});

/**
 * A2: :approve — active, previous superseded, source active (BR-151: 95% of required fields
 * right). PROPOSAL: `field_mappings` carries the person's choice for fields the agent was unsure of.
 */
export const approveCrawlSchema = async (id: string, fieldMappings: Record<string, string>) =>
  v1Send<CrawlSchemaVersion>('POST', `/crawl-schemas/${pathId(id)}:approve`, {
    field_mappings: fieldMappings,
  });
