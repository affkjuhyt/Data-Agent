import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * Data product of the context layer (P07, UC-07-13, context.data_product): the scope of questions
 * the organisation commits to answer right — description and sample questions, certified metrics,
 * normalised datasets, a named owner and steward, a freshness commitment. Answers in scope pass the
 * freshness gate and carry a receipt; others are labelled "Ngoài phạm vi data product, không có
 * biên nhận" (BR-175, BR-178).
 *
 * The spec gives /v1/data-products to this resource and, in the P06 group, to the shared data
 * product of UC-06-06 (lib/v1/dataProducts.ts). Until that is resolved the list carries
 * `layer=context` (PROPOSAL) to ask for this kind. The display fields beside the spec's are
 * PROPOSALs too (docs/v3/05-ui-v3.md §4). The api's rows are read below: what the PROPOSAL
 * fields would say is derived from them or left empty, and the mock sends those fields under
 * `display`, which wins when present.
 */

type Schemas = components['schemas'];

export type ProductStatus = 'draft' | 'pending_review' | 'active' | 'suspended' | 'retired';
export type FreshnessState = 'fresh' | 'overdue' | 'missing';

/** A2: GET /v1/data-products — DataProduct with freshness_state. */
export interface AnswerProductSummary {
  id: string;
  code: string;
  name: string;
  status: ProductStatus;
  version: number;
  owner_name: string;
  steward_name: string;
  freshness_statement: string;
  freshness_state: FreshnessState;
  next_due_at: string | null;
  /** "7/10 câu hỏi mẫu" while a draft is short of the 10 BR-175 needs. */
  readiness_text: string | null;
}

export interface ProductMember {
  id: string;
  member_kind: 'dataset' | 'metric' | 'knowledge_page' | 'shared_product';
  ref: string;
  /** "Dataset · chính · v41", "Chỉ số · ký Nguyễn Thị Hạnh 15/06/2026" */
  detail: string;
  role: 'primary' | 'reference';
  certified: boolean;
}

/** A2: GET /v1/data-products/{id} — with members, freshness, the last eval and open Error Book. */
export interface AnswerProduct extends AnswerProductSummary {
  data_space_name: string;
  description: string;
  scope_chips: string[];
  out_of_scope_note: string;
  sample_questions: { question: string; answer: string; result: 'pass' | 'fail' | 'pending' }[];
  sample_total: number;
  members: ProductMember[];
  stats: {
    /** PROPOSAL: shares of the last 30 days' questions; null without them. */
    in_scope_pct: number | null;
    masked_pct: number | null;
    masked_note: string;
    /** Open Error Book entries: the count from the api, the entries themselves PROPOSAL. */
    error_book_count: number;
    error_book_open: { id: string; code: string }[];
    eval: { passed: number; total: number; threshold_pct: number } | null;
  };
  /** Who can approve: the owner, other than the steward and the creator. */
  reviewer_text: string;
  /** A draft version is being prepared beside the active one. */
  draft_version: number | null;
}

/** A2: GET /v1/data-products/{id}/freshness?periods= — per primary dataset, and the last periods. */
export interface ProductFreshness {
  datasets: {
    ref: string;
    commitment_text: string;
    grace_text: string;
    last_as_of: string;
    last_loaded_at: string;
    next_due_at: string;
    state: FreshnessState;
  }[];
  periods: { period: string; state: 'shown' | 'masked_stale' | 'not_available'; note: string }[];
}

/** PROPOSAL: the Theo dõi tab — scope and masking over 30 days, by week. */
export interface ProductMonitoring {
  weeks: { label: string; in_scope_pct: number; masked_pct: number }[];
  questions_30d: number;
}

type ProductRow = Schemas['services__api__modules__context__schemas__DataProductView'];
type SummaryExtras = Pick<
  AnswerProductSummary,
  'owner_name' | 'steward_name' | 'next_due_at' | 'readiness_text'
>;

export type ProductSummaryWire = ProductRow & Partial<SummaryExtras>;
export type ProductDetailWire = Schemas['DataProductDetail'] & {
  display?: Partial<AnswerProduct>;
};

/** BR-175: a product goes to review with 10 sample questions or more. */
const SAMPLE_MINIMUM = 10;

const samplesOf = (raw: unknown): { text: string }[] =>
  (Array.isArray(raw) ? raw : []).filter(
    (q): q is { text: string } => typeof q === 'object' && q !== null && typeof q.text === 'string',
  );

function readSummary(raw: ProductSummaryWire): AnswerProductSummary {
  const samples = samplesOf(raw.sample_questions).length;
  return {
    id: raw.id,
    code: raw.code,
    name: raw.name,
    status: raw.status as ProductStatus,
    version: raw.version,
    owner_name: raw.owner_name ?? '',
    steward_name: raw.steward_name ?? '',
    freshness_statement: raw.freshness_statement,
    freshness_state: (raw.freshness_state ?? 'missing') as FreshnessState,
    next_due_at: raw.next_due_at ?? null,
    readiness_text:
      raw.readiness_text ??
      (raw.status === 'draft' && samples < SAMPLE_MINIMUM
        ? `${samples}/${SAMPLE_MINIMUM} câu hỏi mẫu`
        : null),
  };
}

const MEMBER_LABEL: Record<string, string> = {
  dataset: 'Dataset',
  metric: 'Chỉ số',
  knowledge_page: 'Trang tri thức',
  shared_product: 'Data product dùng chung',
};

/** `scope_entities` [{dimension, values}] in words: "vùng: Bắc, Nam". */
const chipsOf = (raw: unknown): string[] =>
  (Array.isArray(raw) ? raw : []).flatMap((e) =>
    typeof e === 'object' && e !== null && typeof e.dimension === 'string'
      ? [
          `${e.dimension}${Array.isArray(e.values) && e.values.length ? `: ${e.values.join(', ')}` : ''}`,
        ]
      : [],
  );

/** latest_eval_run is the run's row: its passed and item counts. */
function evalOf(raw: unknown): AnswerProduct['stats']['eval'] {
  if (typeof raw !== 'object' || raw === null) return null;
  const { passed, item_count } = raw as { passed?: unknown; item_count?: unknown };
  return typeof passed === 'number' && typeof item_count === 'number' && item_count > 0
    ? { passed, total: item_count, threshold_pct: 95 }
    : null;
}

function readProduct(raw: ProductDetailWire): AnswerProduct {
  const samples = samplesOf(raw.sample_questions);
  const derived: AnswerProduct = {
    ...readSummary(raw),
    data_space_name: '',
    description: raw.description,
    scope_chips: chipsOf(raw.scope_entities),
    out_of_scope_note: raw.out_of_scope_note ?? '',
    sample_questions: samples.map((q) => ({ question: q.text, answer: '', result: 'pending' })),
    sample_total: samples.length,
    members: (raw.members ?? []).map((m) => ({
      id: m.id,
      member_kind: m.member_kind as ProductMember['member_kind'],
      ref: m.member_ref,
      detail: `${MEMBER_LABEL[m.member_kind] ?? m.member_kind} · ${m.role === 'primary' ? 'chính' : 'tham chiếu'}`,
      role: m.role as ProductMember['role'],
      certified: m.classification_confirmed_by !== null,
    })),
    stats: {
      in_scope_pct: null,
      masked_pct: null,
      masked_note: '',
      error_book_count: raw.open_error_book_entries ?? 0,
      error_book_open: [],
      eval: evalOf(raw.latest_eval_run),
    },
    reviewer_text: 'Người duyệt: owner, khác steward và người tạo.',
    draft_version: null,
  };
  return { ...derived, ...raw.display };
}

/** The api lists the context layer's products only (A2 has no read for published ones). */
export const listAnswerProducts = async (status?: ProductStatus) => {
  const page = await v1Get<Page<ProductSummaryWire>>('/data-products', {
    layer: 'context',
    status,
    page_size: 100,
  });
  return {
    ...page,
    items: page.items.map(readSummary),
  };
};

export const getAnswerProduct = async (id: string) =>
  readProduct(await v1Get<ProductDetailWire>(`/data-products/${pathId(id)}`));

/** PROPOSAL: the commitment and grace in words; the mock sends them under `display`. */
type FreshnessWire = Schemas['FreshnessReport'] & { display?: ProductFreshness };

const SLA_TEXT: Record<string, string> = {
  schedule: 'Theo lịch',
  event: 'Theo sự kiện',
  max_age: 'Không cũ hơn hạn tối đa',
};

function readFreshness(raw: FreshnessWire): ProductFreshness {
  if (raw.display) return raw.display;
  return {
    datasets: raw.datasets.map((d) => ({
      ref: d.dataset_id,
      commitment_text: SLA_TEXT[d.sla_kind] ?? d.sla_kind,
      grace_text: '',
      last_as_of: d.last_as_of ?? '',
      last_loaded_at: d.last_loaded_at ?? '',
      next_due_at: d.next_due_at ?? '',
      state: d.state as FreshnessState,
    })),
    periods: (raw.datasets[0]?.periods ?? []).map((p) => ({
      period: p.period,
      state: p.state,
      note: '',
    })),
  };
}

export const getProductFreshness = async (id: string) =>
  readFreshness(
    await v1Get<FreshnessWire>(`/data-products/${pathId(id)}/freshness`, { periods: 8 }),
  );

export const getProductMonitoring = async (id: string) =>
  v1Get<ProductMonitoring>(`/data-products/${pathId(id)}/monitoring`);

/** A2: PATCH on an active product returns the draft version + 1; the old one serves until approved. */
export const draftProductVersion = async (id: string) =>
  v1Send<ProductRow>('PATCH', `/data-products/${pathId(id)}`, {});

/** A2: :submit runs the product's eval; at 95% on 10+ questions it goes to Cần bạn (BR-175). */
export const submitAnswerProduct = async (id: string, note?: string) =>
  v1Send<Schemas['DataProductSubmitted']>('POST', `/data-products/${pathId(id)}:submit`, { note });

/** A2: DELETE a member; refused for the primary of an active version (a new version is needed). */
export const removeProductMember = async (id: string, memberId: string) =>
  v1Send<void>('DELETE', `/data-products/${pathId(id)}/members/${pathId(memberId)}`);
