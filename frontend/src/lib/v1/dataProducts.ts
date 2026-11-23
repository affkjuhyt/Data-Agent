import { listDatasets } from './api';
import { type Page, V1Error, api, ok, okAs } from './client';
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
    /** null when the mart does not say how fresh it is (MartView has no freshness). */
    freshness_text: string | null;
    quality_text: string;
    /** null when who reads it is not known (MartView names no consumer). */
    consumers_text: string | null;
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
  const display = (raw as DataMartWire & { display?: DataMart }).display;
  if (display) return display;
  if (!raw.fact_dataset_id) return raw as unknown as DataMart;
  const dimensions = Array.isArray(raw.dimensions) ? raw.dimensions : [];
  const measures = Array.isArray(raw.measures) ? raw.measures : [];
  const proposed = Array.isArray(raw.suggested_dimensions) ? raw.suggested_dimensions : [];
  const tableOf = (value: unknown, fallback: string) =>
    typeof value === 'object' && value !== null
      ? (value as Record<string, unknown>)
      : { name: fallback };
  const factName = datasets.get(raw.fact_dataset_id) ?? raw.fact_dataset_id;
  const measureLines = measures.flatMap((value) => {
    const row = tableOf(value, '');
    const name = textOf(row.name, '');
    return name ? [row.agg ? `${name} (${String(row.agg)})` : name] : [];
  });
  const dimensionRows = [...dimensions, ...proposed].map((value, index) => {
    const row = tableOf(value, `dimension_${index + 1}`);
    const name = textOf(row.name ?? row.label, `dimension_${index + 1}`);
    const key = textOf(row.key, '');
    const cols = linesOf(row.columns ?? row.fields);
    return {
      name,
      lines: cols.length ? cols : key ? [key] : [],
      suggested: index >= dimensions.length,
    };
  });
  const productName = raw.name
    .replace(/\W+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
  const accepted = dimensionRows.filter((d) => !d.suggested);
  return {
    id: raw.id,
    name: raw.name,
    summary_text: `${raw.grain} · ${measures.length} độ đo · ${accepted.length} chiều`,
    fact: {
      name: factName,
      lines: [raw.grain, ...measureLines].filter(Boolean),
    },
    // Keep suggested dims on the canvas (dashed); the dialog still reads mart.suggestion.
    dimensions: dimensionRows,
    product_draft: {
      name: productName || 'data_product',
      version: '1',
      version_text: 'v1',
      contract_text: `${measures.length} độ đo · ${accepted.length} chiều`,
      // MartView says neither how fresh the mart is nor who reads it: nothing is stated.
      freshness_text: null,
      quality_text: raw.relation_check_passed ? 'Đã kiểm tra liên kết' : 'Chưa kiểm tra liên kết',
      consumers_text: null,
      breaking_note: null,
    },
    suggestion: proposed.length
      ? {
          dimensions: proposed.map((value, index) => {
            const row = tableOf(value, `dimension_${index + 1}`);
            const name = textOf(row.name, `dimension_${index + 1}`);
            const label = textOf(row.label, name);
            const key = textOf(row.key, '');
            return {
              key: name,
              label: key ? `${label} · ${key}` : label,
            };
          }),
          measures: [],
        }
      : null,
  };
}

const tablesFor = (
  datasets: Page<
    components['schemas']['services__api__modules__catalog__schemas__DatasetView']
  > | null,
) =>
  new Map<string, string>(
    datasets?.items.map((d) => [d.id, `${d.schema_name}.${d.table_name}`]) ?? [],
  );

/** GET /v1/marts, adapted from the API's DataMartView. */
export const listMarts = async (): Promise<Page<Pick<DataMart, 'id' | 'name'>>> => {
  const [page, datasets] = await Promise.all([
    ok<Page<DataMartWire>>(api.GET('/v1/marts')),
    ok<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      api.GET('/v1/datasets', { params: { query: { page_size: 500 } } }),
    ).catch(() => null),
  ]);
  const tables = tablesFor(datasets);
  return { ...page, items: page.items.map((m) => readDataMart(m, tables)) };
};

/** PROPOSAL: GET /v1/marts/{id}. */
export async function getMart(id: string) {
  const [mart, datasets] = await Promise.all([
    ok<DataMartWire>(api.GET('/v1/marts/{id}', { params: { path: { id } } })),
    ok<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      api.GET('/v1/datasets', { params: { query: { page_size: 500 } } }),
    ).catch(() => null),
  ]);
  return readDataMart(mart, tablesFor(datasets));
}

type ColumnView = Schemas['ColumnView'];

/** The fact table's columns, as the catalog holds them. */
const factColumns = async (datasetId: string): Promise<ColumnView[]> =>
  (await ok(api.GET('/v1/datasets/{id}', { params: { path: { id: datasetId } } }))).columns ?? [];

/** The number columns a mart can sum: not a key, nor a foreign key (`…_key`, `…_id`). */
export const measureColumns = (
  columns: Pick<ColumnView, 'name' | 'normalized_type' | 'is_key'>[],
) =>
  columns
    .filter(
      (c) =>
        !c.is_key &&
        !/(_key|_id)$/i.test(c.name) &&
        (c.normalized_type === 'integer' || c.normalized_type === 'decimal'),
    )
    .map((c) => c.name);

export const listMeasureColumns = async (datasetId: string) =>
  measureColumns(await factColumns(datasetId));

/**
 * A published product commits to the mart's columns: each dimension's key on the fact and each
 * measure, typed as the fact table has them (UC-06-06 step 4). Nothing is made up: a mart with
 * neither gives no columns, and the api refuses an empty contract.
 */
export function martContract(
  mart: Pick<DataMartWire, 'dimensions' | 'measures'>,
  columns: Pick<ColumnView, 'name' | 'normalized_type'>[],
) {
  const nameOf = (value: unknown, field: 'key' | 'name') =>
    typeof value === 'object' && value !== null
      ? String((value as Record<string, unknown>)[field] ?? '')
      : '';
  const names = [
    ...(Array.isArray(mart.dimensions) ? mart.dimensions : []).map((d) => nameOf(d, 'key')),
    ...(Array.isArray(mart.measures) ? mart.measures : []).map((m) => nameOf(m, 'name')),
  ].filter(Boolean);
  return {
    columns: [...new Set(names)].map((name) => ({
      name,
      type: columns.find((c) => c.name === name)?.normalized_type ?? 'string',
    })),
  };
}

/** A2: POST /v1/marts — empty dimensions bootstrap from the fact's active relationships. */
export async function createMart(body: {
  name: string;
  fact_dataset_id: string;
  grain: string;
  measures: { name: string; agg?: string }[];
}) {
  const [raw, datasets] = await Promise.all([
    ok<DataMartWire>(
      api.POST('/v1/marts', {
        body: {
          name: body.name,
          fact_dataset_id: body.fact_dataset_id,
          grain: body.grain,
          measures: body.measures,
          dimensions: [],
          relations: [],
          checks: [],
        },
      }),
    ),
    ok<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      api.GET('/v1/datasets', { params: { query: { page_size: 500 } } }),
    ).catch(() => null),
  ]);
  return readDataMart(raw, tablesFor(datasets));
}

/** Accept the selected agent suggestions through the canonical mart PATCH contract. */
export async function applyMartSuggestion(
  id: string,
  body: { dimensions: string[]; measures: string[] },
) {
  const [mart, datasets] = await Promise.all([
    ok<DataMartWire>(api.GET('/v1/marts/{id}', { params: { path: { id } } })),
    ok<Page<components['schemas']['services__api__modules__catalog__schemas__DatasetView']>>(
      api.GET('/v1/datasets', { params: { query: { page_size: 500 } } }),
    ).catch(() => null),
  ]);
  const proposed = Array.isArray(mart.suggested_dimensions) ? mart.suggested_dimensions : [];
  const picked = proposed.filter((value) => {
    if (typeof value !== 'object' || value === null) return false;
    const name = String((value as { name?: string }).name ?? '');
    return body.dimensions.includes(name);
  }) as Record<string, unknown>[];
  const selectedMeasures = body.measures.map((name) => ({ name }));
  const nextRelations = [
    ...(Array.isArray(mart.relations) ? mart.relations : []),
    ...picked.map((row) => {
      const key = String(row.key ?? '');
      const table = String(row.name ?? '');
      const cols = Array.isArray(row.columns) ? row.columns.map(String) : [];
      const toCol = cols[1] ?? cols[0] ?? 'id';
      return { from: key, to: `${table}.${toCol}` };
    }),
  ];
  const updated = await ok<DataMartWire>(
    api.PATCH('/v1/marts/{id}', {
      params: { path: { id } },
      body: {
        dimensions: [...(Array.isArray(mart.dimensions) ? mart.dimensions : []), ...picked],
        measures: [...(Array.isArray(mart.measures) ? mart.measures : []), ...selectedMeasures],
        relations: nextRelations,
      },
    }),
  );
  return readDataMart(updated, tablesFor(datasets));
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

/** pipeline.data_product as GET /v1/data-products?layer=published sends it, one row per version. */
export type PublishedProductWire = Schemas['PublishedDataProductView'];
/** The mock sends the screen's row under `display`. */
type PublishedListWire = PublishedProductWire & { display?: DataProduct };

const VERSION_STATE: Record<string, VersionState> = {
  draft: 'draft',
  published: 'current',
  deprecated: 'supported',
  retired: 'retired',
};

const contractColumns = (contract: unknown) => {
  const columns = (contract as { columns?: unknown } | null)?.columns;
  return Array.isArray(columns)
    ? columns.flatMap((c) =>
        c && typeof c === 'object' && 'name' in c
          ? [{ name: String(c.name), type: String((c as { type?: unknown }).type ?? '') }]
          : [],
      )
    : [];
};

/** The versions of one code, newest first; the one it serves, and a draft above that. */
function versionsOf(rows: PublishedProductWire[], code: string) {
  const versions = rows.filter((r) => r.code === code).sort((a, b) => b.version - a.version);
  const serving = versions.find((r) => r.status === 'published' || r.status === 'deprecated');
  const draft = versions.find(
    (r) => r.status === 'draft' && (!serving || r.version > serving.version),
  );
  return { versions, serving, draft };
}

/**
 * Each product once, at the version it serves (the newest draft when none is served yet), with
 * a draft above it as its next version. Quality is not measured per refresh on the api: null.
 */
export function readPublishedProducts(rows: PublishedProductWire[]): DataProduct[] {
  return [...new Set(rows.map((r) => r.code))].flatMap((code) => {
    const { versions, serving, draft } = versionsOf(rows, code);
    const row = serving ?? versions[0];
    if (!row) return [];
    return [
      {
        id: row.id,
        name: row.name,
        code: row.code,
        version: String(row.version),
        next_version: serving && draft ? String(draft.version) : null,
        description: '',
        status: serving ? 'published' : 'draft',
        breaking_change: Boolean(serving && draft?.breaking_notice_at),
        used_by_spaces: serving ? (serving.consumers ?? []).length : null,
        quality_pct: null,
        refreshed_at: row.updated_at,
      } satisfies DataProduct,
    ];
  });
}

/**
 * The drawer of a published product from its version rows: the contract it serves (a column the
 * next draft drops or retypes is `changed`), the Data Spaces reading it, and its versions. The
 * api records no refresh checks and no agent note for this layer, so those stay empty.
 */
export function readPublishedDetail(
  rows: PublishedProductWire[],
  id: string,
  spaceNames: ReadonlyMap<string, string>,
): DataProductDetail | null {
  const picked = rows.find((r) => r.id === id);
  const summary = picked && readPublishedProducts(rows).find((p) => p.code === picked.code);
  if (!picked || !summary) return null;
  const { versions, serving, draft } = versionsOf(rows, picked.code);
  const row = serving ?? picked;
  const next = draft && draft !== row ? contractColumns(draft.contract) : null;
  const consumers = row.consumers ?? [];
  return {
    ...summary,
    contract: contractColumns(row.contract).map((c) => ({
      ...c,
      description: '',
      changed: next !== null && !next.some((n) => n.name === c.name && n.type === c.type),
    })),
    checks: [],
    checked_at: '',
    consumers: consumers.map((space) => ({
      data_space_name: spaceNames.get(space) ?? 'Data Space khác',
      used_for: '',
      version: String(row.version),
    })),
    consumer_spaces_total: consumers.length,
    affected_by_next: draft?.breaking_notice_at ? consumers.length : 0,
    versions: versions.map((v) => ({
      version: String(v.version),
      change: v.breaking_notice_at ? 'Thay đổi phá vỡ hợp đồng' : '',
      state: VERSION_STATE[v.status] ?? 'retired',
    })),
    agent_note: null,
  };
}

const listPublishedRows = async () =>
  (
    await okAs<Page<PublishedListWire>>(
      api.GET('/v1/data-products', { params: { query: { layer: 'published', page_size: 500 } } }),
    )
  ).items;

/** GET /v1/data-products?layer=published: the P06 products of the Data Space in use. */
export const listDataProducts = async (): Promise<Page<DataProduct>> => {
  const rows = await listPublishedRows();
  const shown =
    rows.length > 0 && rows.every((r) => r.display)
      ? rows.map((r) => r.display!)
      : readPublishedProducts(rows);
  return { items: shown, next_page_token: '' };
};

/**
 * One published product. GET /v1/data-products/{id} reads the context layer only, so a product
 * it does not know is read from the layer's list (the mock still answers the detail route).
 */
export async function getDataProduct(id: string): Promise<DataProductDetail> {
  try {
    return await okAs<DataProductDetail>(
      api.GET('/v1/data-products/{id}', { params: { path: { id } } }),
    );
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
  }
  const [rows, spaces] = await Promise.all([
    listPublishedRows(),
    ok(api.GET('/v1/data-spaces')).catch(() => null),
  ]);
  const names = new Map((spaces?.items ?? []).map((s) => [s.id, s.name] as const));
  const detail = readPublishedDetail(rows, id, names);
  if (!detail) throw new V1Error(404, 'not_found', 'Không tìm thấy sản phẩm dữ liệu.');
  return detail;
}

/** PROPOSAL: POST /v1/data-products/{id}:draft-version — the agent drafts the next version. */
export async function draftNextVersion(id: string) {
  return okAs<DataProductDetail>(
    api.POST('/v1/data-products/{id}:draft-version', { params: { path: { id } } }),
  );
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
  const [me, mart] = await Promise.all([
    getMe(),
    ok<DataMartWire>(api.GET('/v1/marts/{id}', { params: { path: { id: body.mart_id } } })),
  ]);
  const contract = martContract(mart, await factColumns(mart.fact_dataset_id));
  if (contract.columns.length === 0)
    throw new Error('Mart chưa có chiều hay độ đo nào để cam kết trong hợp đồng sản phẩm.');
  return ok<{ id: string }>(
    api.POST('/v1/data-products', {
      body: {
        code: codeOf(body.name),
        name: body.name,
        layer: 'published',
        data_mart_id: body.mart_id,
        owner_id: me.id,
        contract,
      } satisfies components['schemas']['DataProductCreate'],
    }),
  );
};

/** A2: POST /v1/data-products/{id}:publish — the product row, its version a number there. */
export async function publishDataProduct(
  id: string,
): Promise<Pick<DataProduct, 'id' | 'name' | 'status' | 'version'>> {
  const raw = await ok<{ id: string; name: string; status: string; version: number | string }>(
    api.POST('/v1/data-products/{id}:publish', { params: { path: { id } } }),
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
export const listCatalogProducts = () =>
  okAs<Page<CatalogProduct>>(api.GET('/v1/data-products/catalog'));

/** PROPOSAL: one access request per product, decided by the owning Data Space. */
interface ProductUseRequest {
  id: string;
  product_id: string;
  status: 'pending' | 'approved';
}

/**
 * PROPOSAL: POST /v1/data-products:request-use → 202 with the access requests created; the
 * products become readable in `data_space_id` once each owner approves.
 */
export const requestProductUse = (body: { product_ids: string[]; data_space_id: string }) =>
  okAs<{ requests: ProductUseRequest[] }>(api.POST('/v1/data-products:request-use', { body }));

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
  compound_unique: 'Không trùng theo khoá',
  match_regex: 'Đúng mẫu',
};

type RuleWire = Schemas['QualityRuleView'] & { display?: QualityRule };
type ResultRow = Schemas['QualityResultView'];

const ORIGIN: Record<string, string> = {
  manual: 'Người tạo',
  agent_suggested: 'Agent đề xuất',
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
    // Active rules only: a draft or a disabled rule does not run, yet its old failures were
    // counted in "N quy tắc · M đang vi phạm" (rules are created active, ingest quality_rule).
    ok<Page<RuleWire>>(
      api.GET('/v1/quality-rules', { params: { query: { status: 'active', page_size: 500 } } }),
    ),
    listDatasets(),
    ok<Page<ResultRow>>(
      api.GET('/v1/quality-results', { params: { query: { from: week, page_size: 500 } } }),
    ),
  ]);
  const tables = new Map(datasets.items.map((d) => [d.id, d.display_name]));
  return { ...rules, items: rules.items.map((r) => readRule(r, tables, results.items)) };
};

/** A2: POST /v1/quality-rules (QualityRuleCreate); the data-quality agent runs it right away. */
export const createQualityRule = (body: {
  dataset_id: string;
  kind: RuleKind;
  column_name?: string;
  /** range: {min, max}; not_null: {where: {column: values}}; in_list: {values}. */
  params?: Record<string, unknown>;
  severity: RuleSeverity;
}) =>
  ok<Schemas['QualityRuleView']>(
    api.POST('/v1/quality-rules', {
      body: {
        ...body,
        severity: body.severity === 'block' ? 'high' : 'medium',
      },
    }),
  );
