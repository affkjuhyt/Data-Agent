import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

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

/** PROPOSAL: GET /v1/marts (A2 only creates marts). */
export const listMarts = () => v1Get<Page<Pick<DataMart, 'id' | 'name'>>>('/marts');

/** PROPOSAL: GET /v1/marts/{id}. */
export async function getMart(id: string) {
  return v1Get<DataMart>(`/marts/${pathId(id)}`);
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

/** A2: POST /v1/data-products with layer=published — a product made from a mart. */
export const createDataProduct = (body: { mart_id: string; name: string; version: string }) =>
  v1Send<{ id: string }>('POST', '/data-products', {
    code: codeOf(body.name),
    name: body.name,
    layer: 'published',
    data_mart_id: body.mart_id,
  } satisfies components['schemas']['DataProductCreate']);

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

/** A2: GET /v1/quality-rules. */
export const listQualityRules = () => v1Get<Page<QualityRule>>('/quality-rules');

/** A2: POST /v1/quality-rules; the owner is the caller until reassigned. */
export const createQualityRule = (body: { name: string; target: string; severity: RuleSeverity }) =>
  v1Send<QualityRule>('POST', '/quality-rules', body);
