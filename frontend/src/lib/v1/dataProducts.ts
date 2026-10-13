import { listDatasets } from './api';
import { pathId, v1Get, v1Send, type Page } from './client';
import { getMe } from './me';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * P06 Chuẩn bị dữ liệu, second half: data marts and data products (UC-06-06) and quality rules
 * (UC-06-07). A2 has POST /v1/marts, POST /v1/data-products, POST /v1/data-products/{id}:publish,
 * GET/POST /v1/quality-rules, PATCH /v1/quality-rules/{id} and GET /v1/quality-results; every
 * read of a mart or a product below is a PROPOSAL, as are the display fields the screens need.
 */

/* ─── Data mart (catalog.data_mart, a star model) ─────────────────────── */

export interface MartTable {
  name: string;
  /** PROPOSAL: the columns as the diagram writes them, a few per line ("loan_key · date_key"). */
  lines: string[];
}

interface MartDimension extends MartTable {
  /** PROPOSAL: proposed by the agent from frequent questions, drawn dashed until accepted. */
  suggested: boolean;
}

export type DataMartWire = components['schemas']['DataMartView'];

/** One item the agent proposes adding to a mart ("Hộp thoại · suggestMart"). */
interface MartSuggestionItem {
  key: string;
  label: string;
}

export interface DataMart {
  id: string;
  /** "mart.credit_risk" */
  name: string;
  /** PROPOSAL: "1 bảng sự kiện · 5 chiều · 14 độ đo · cập nhật sau pipeline lúc 06:20". */
  summary_text: string;
  fact: MartTable;
  dimensions: MartDimension[];
  /** PROPOSAL: what publishing this mart as a data product would commit to. */
  product_draft: {
    name: string;
    version: string;
    version_text: string;
    contract_text: string;
    freshness_text: string;
    quality_text: string;
    consumers_text: string;
    /** Set when the new version breaks the current one (old version runs alongside 30 days). */
    breaking_note: string | null;
  };
  /** PROPOSAL: the agent's open suggestion, null once applied or dismissed. */
  suggestion: { dimensions: MartSuggestionItem[]; measures: MartSuggestionItem[] } | null;
}

const textOf = (value: unknown, fallback: string): string =>
  typeof value === 'string' && value.trim() ? value : fallback;

const linesOf = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.flatMap((item) =>
      typeof item === 'string'
        ? [item]
        : typeof item === 'object' && item !== null
          ? [textOf((item as Record<string, unknown>).name, '—')]
          : [],
    );
  }
  return [];
};

/** Adapt A3's DataMartView to the star-model presentation shape. */
export function readDataMart(raw: DataMartWire, datasets: Map<string, string>): DataMart {
  if (!raw.fact_dataset_id) return raw as unknown as DataMart;
  const dimensions = Array.isArray(raw.dimensions) ? raw.dimensions : [];
  const measures = Array.isArray(raw.measures) ? raw.measures : [];
  const proposed = Array.isArray(raw.suggested_dimensions) ? raw.suggested_dimensions : [];
  const tableOf = (value: unknown, fallback: string) =>
    typeof value === 'object' && value !== null
      ? (value as Record<string, unknown>)
      : { name: fallback };
  const fact = tableOf(null, datasets.get(raw.fact_dataset_id) ?? raw.fact_dataset_id);
  const dimensionRows = [...dimensions, ...proposed].map((value, index) => {
    const row = tableOf(value, `dimension_${index + 1}`);
    const name = textOf(row.name ?? row.label, `dimension_${index + 1}`);
    return {
      name,
      lines: linesOf(row.columns ?? row.fields),
      suggested: index >= dimensions.length,
    };
  });
  const productName = raw.name.replace(/\W+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();
  return {
    id: raw.id,
    name: raw.name,
    summary_text: `${raw.grain} · ${measures.length} độ đo · ${dimensionRows.length} chiều`,
    fact: { name: textOf(fact.name, raw.fact_dataset_id), lines: [raw.grain] },
    dimensions: dimensionRows.filter((d) => !d.suggested),
    product_draft: {
      name: productName || 'data_product',
      version: '1',
      version_text: 'v1',
      contract_text: `${measures.length} độ đo · ${dimensionRows.length} chiều`,
      freshness_text: 'Cập nhật theo pipeline',
      quality_text: raw.relation_check_passed ? 'Đã kiểm tra liên kết' : 'Chưa kiểm tra liên kết',
      consumers_text: 'Chưa có người dùng',
      breaking_note: null,
    },
    suggestion: proposed.length
      ? {
          dimensions: proposed.map((value, index) => {
            const row = tableOf(value, `dimension_${index + 1}`);
            return { key: textOf(row.name, `dimension_${index + 1}`), label: textOf(row.label, '') };
          }),
          measures: [],
        }
      : null,
  };
}

const tablesFor = (datasets: Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']> | null) =>
  new Map<string, string>(
    datasets?.items.map((d) => [d.id, `${d.schema_name}.${d.table_name}`]) ?? [],
  );

/** GET /v1/marts, adapted from the API's DataMartView. */
export const listMarts = async (): Promise<Page<Pick<DataMart, 'id' | 'name'>>> => {
  const [page, datasets] = await Promise.all([
    v1Get<Page<DataMartWire>>('/marts'),
    v1Get<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      '/datasets',
      { page_size: 500 },
    ).catch(() => null),
  ]);
  const tables = tablesFor(datasets);
  return { ...page, items: page.items.map((m) => readDataMart(m, tables)) };
};

/** PROPOSAL: GET /v1/marts/{id}. */
export async function getMart(id: string) {
  const [mart, datasets] = await Promise.all([
    v1Get<DataMartWire>(`/marts/${pathId(id)}`),
    v1Get<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      '/datasets',
      { page_size: 500 },
    ).catch(() => null),
  ]);
  return readDataMart(mart, tablesFor(datasets));
}

/** PROPOSAL: POST /v1/marts/{id}:apply-suggestion — add the ticked dimensions and measures. */
export async function applyMartSuggestion(
  id: string,
  body: { dimensions: string[]; measures: string[] },
) {
  return v1Send<DataMart>('POST', `/marts/${pathId(id)}:apply-suggestion`, body);
}

/* ─── Data product (catalog.data_product) ─────────────────────────────── */

/** PROPOSAL: pending_approval = :publish needs an approver (a breaking change, or no `approve`). */
type DataProductStatus = 'draft' | 'pending_approval' | 'published';

export interface DataProduct {
  id: string;
  /** "Biên LN theo ngành hàng" */
  name: string;
  /** "sales_margin" */
  code: string;
  version: string;
  /** PROPOSAL: a draft version on top of the current one ("v2.3 → v3"). */
  next_version: string | null;
  description: string;
  status: DataProductStatus;
  /** PROPOSAL: the next version changes the meaning or shape of a column in use. */
  breaking_change: boolean;
  /** PROPOSAL: Data Spaces reading it; null while it has never been published. */
  used_by_spaces: number | null;
  /** PROPOSAL: share of quality checks passed on the last refresh, 0–100. */
  quality_pct: number | null;
  /** PROPOSAL: last refresh. */
  refreshed_at: string;
}

export type CheckState = 'pass' | 'warn' | 'fail';
export type VersionState = 'draft' | 'current' | 'supported' | 'retired';

export interface DataProductDetail extends DataProduct {
  /** The contract: one row per committed column; `changed` = differs in the next version. */
  contract: { name: string; type: string; description: string; changed: boolean }[];
  /** PROPOSAL: the checks of the last refresh, in words. */
  checks: { text: string; state: CheckState }[];
  checked_at: string;
  /** PROPOSAL: who reads it, per Data Space. */
  consumers: { data_space_name: string; used_for: string; version: string }[];
  consumer_spaces_total: number;
  /** Data Spaces the next version would affect. */
  affected_by_next: number;
  versions: { version: string; change: string; state: VersionState }[];
  /** PROPOSAL: the agent's reading of a pending change, with the draft it offers to make. */
  agent_note: { text: string; action_label: string } | null;
}

/**
 * PROPOSAL: the published products' list. The api's GET /v1/data-products serves the context
 * layer (P07, lib/v1/answerProducts.ts) and has no read for published ones yet, so its context
 * rows are left out here and the list is empty until A2 adds one.
 */
export const listDataProducts = async () => {
  const page = await v1Get<Page<DataProduct & { layer?: string }>>('/data-products');
  return { ...page, items: page.items.filter((p) => p.layer !== 'context') };
};

/** PROPOSAL: GET /v1/data-products/{id}. */
export async function getDataProduct(id: string) {
  return v1Get<DataProductDetail>(`/data-products/${pathId(id)}`);
}

/** PROPOSAL: POST /v1/data-products/{id}:draft-version — the agent drafts the next version. */
export async function draftNextVersion(id: string) {
  return v1Send<DataProductDetail>('POST', `/data-products/${pathId(id)}:draft-version`);
}

/** "Biên LN theo ngành hàng" → "bien_ln_theo_nganh_hang" */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || 'data_product';

/**
 * A2: POST /v1/data-products with layer=published — a product made from a mart. A product has a
 * named owner (decision of 12/10/2026): whoever publishes it from the mart, until reassigned.
 */
export const createDataProduct = async (body: {
  mart_id: string;
  name: string;
  version: string;
}) => {
  const me = await getMe();
  return v1Send<{ id: string }>('POST', '/data-products', {
    code: codeOf(body.name),
    name: body.name,
    layer: 'published',
    data_mart_id: body.mart_id,
    owner_id: me.id,
  } satisfies components['schemas']['DataProductCreate']);
};

/** A2: POST /v1/data-products/{id}:publish — the product row, its version a number there. */
export async function publishDataProduct(
  id: string,
): Promise<Pick<DataProduct, 'id' | 'name' | 'status' | 'version'>> {
  const raw = await v1Send<{ id: string; name: string; status: string; version: number | string }>(
    'POST',
    `/data-products/${pathId(id)}:publish`,
  );
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status as DataProduct['status'],
    version: String(raw.version),
  };
}

/* ─── Catalog: products of other Data Spaces ("Hộp thoại · importCatalog" 2095:9055) ─── */

/** PROPOSAL: a published product of another Data Space, as the shared catalog lists it. */
export interface CatalogProduct {
  id: string;
  /** "customer_360" */
  code: string;
  name: string;
  version: string;
  /** The Data Space that owns it and grants read access. */
  owner_space: { id: string; name: string };
  /** Carries personal data: reading it needs the owner's approval. */
  has_pii: boolean;
}

/** PROPOSAL: GET /v1/data-products/catalog — published products of the other Data Spaces. */
export const listCatalogProducts = () => v1Get<Page<CatalogProduct>>('/data-products/catalog');

/** PROPOSAL: one access request per product, decided by the owning Data Space. */
export interface ProductUseRequest {
  id: string;
  product_id: string;
  status: 'pending' | 'approved';
}

/**
 * PROPOSAL: POST /v1/data-products:request-use → 202 with the access requests created; the
 * products become readable in `data_space_id` once each owner approves.
 */
export const requestProductUse = (body: { product_ids: string[]; data_space_id: string }) =>
  v1Send<{ requests: ProductUseRequest[] }>('POST', '/data-products:request-use', body);

/* ─── Quality rule (ingest.quality_rule / quality_result) ─────────────── */

export type RuleSeverity = 'block' | 'warn';

export interface QualityRule {
  id: string;
  /** "Dư nợ không âm" */
  name: string;
  /** Table or column it applies to: "prep.loans_retail.outstanding_amt". */
  target: string;
  severity: RuleSeverity;
  owner: { id: string; name: string };
  /** PROPOSAL: the worst result of each of the last 7 days, oldest first; null = did not run. */
  last_7d: (CheckState | null)[];
  /** PROPOSAL: the latest result; null before the first run. */
  current: { state: CheckState; value_text: string | null } | null;
}

export type RuleKind = Schemas['QualityRuleCreate']['kind'];

export const RULE_KIND: Record<RuleKind, string> = {
  not_null: 'Không rỗng',
  range: 'Trong khoảng',
  unique: 'Không trùng',
  in_list: 'Thuộc danh sách',
  sum_matches: 'Tổng khớp',
  freshness: 'Độ mới',
  custom_sql: 'SQL tuỳ chỉnh',
};

type RuleWire = Schemas['QualityRuleView'] & { display?: QualityRule };
type ResultRow = Schemas['QualityResultView'];

const ORIGIN: Record<string, string> = {
  manual: 'Người tạo',
  agent_suggested: 'Agent đề xuất',
  pack: 'Gói ngành',
};

/** high blocks the report (BR-116); medium and low warn. */
const severityOf = (severity: string): RuleSeverity => (severity === 'high' ? 'block' : 'warn');

/** A failed check is a failure where the rule blocks and a warning where it does not. */
const stateOf = (result: ResultRow): CheckState =>
  result.passed ? 'pass' : severityOf(result.severity) === 'block' ? 'fail' : 'warn';
const WORST: CheckState[] = ['pass', 'warn', 'fail'];

function ruleName(raw: Schemas['QualityRuleView']) {
  const params = (raw.params ?? {}) as Record<string, unknown>;
  const kind = RULE_KIND[raw.kind as RuleKind] ?? raw.kind;
  return raw.kind === 'range' && (params.min !== undefined || params.max !== undefined)
    ? `${kind} ${params.min ?? '…'} – ${params.max ?? '…'}`
    : kind;
}

/*
 * A rule as the screen lists it: its name is its kind (and range), where it applies is the
 * dataset and column, the owner is who made it (A3 keeps the origin, not a person). The last
 * seven days and the current state are the rule's results of the past week, the worst of each
 * day; a day without a check stays empty.
 */
function readRule(raw: RuleWire, tables: Map<string, string>, results: ResultRow[]): QualityRule {
  if (raw.display) return raw.display;
  const mine = results
    .filter((r) => r.rule_id === raw.id)
    .sort((a, b) => b.checked_at.localeCompare(a.checked_at));
  const today = new Date();
  const last_7d = Array.from({ length: 7 }, (_, i) => {
    const day = new Date(today);
    day.setDate(today.getDate() - (6 - i));
    const key = day.toISOString().slice(0, 10);
    const states = mine.filter((r) => r.checked_at.slice(0, 10) === key).map(stateOf);
    return states.length ? WORST[Math.max(...states.map((st) => WORST.indexOf(st)))] : null;
  });
  const latest = mine[0];
  const table = tables.get(raw.dataset_id) ?? raw.dataset_id;
  return {
    id: raw.id,
    name: ruleName(raw),
    target: raw.column_name ? `${table}.${raw.column_name}` : table,
    severity: severityOf(raw.severity),
    owner: { id: '', name: ORIGIN[raw.origin] ?? raw.origin },
    last_7d,
    current: latest
      ? {
          state: stateOf(latest),
          value_text: latest.violations ? `${latest.violations} dòng vi phạm` : null,
        }
      : null,
  };
}

/** A2: GET /v1/quality-rules, with the datasets they apply to and the past week's results. */
export const listQualityRules = async (): Promise<Page<QualityRule>> => {
  const week = new Date(Date.now() - 7 * 86_400_000).toISOString();
  const [rules, datasets, results] = await Promise.all([
    v1Get<Page<RuleWire>>('/quality-rules', { page_size: 500 }),
    listDatasets(),
    v1Get<Page<ResultRow>>('/quality-results', { from: week, page_size: 500 }),
  ]);
  const tables = new Map(datasets.items.map((d) => [d.id, d.display_name]));
  return { ...rules, items: rules.items.map((r) => readRule(r, tables, results.items)) };
};

/** A2: POST /v1/quality-rules (QualityRuleCreate); the data-quality agent runs it right away. */
export const createQualityRule = (body: {
  dataset_id: string;
  kind: RuleKind;
  column_name?: string;
  params?: Record<string, number>;
  severity: RuleSeverity;
}) =>
  v1Send<Schemas['QualityRuleView']>('POST', '/quality-rules', {
    ...body,
    severity: body.severity === 'block' ? 'high' : 'medium',
  });
