import { pathId, v1Get, v1Send, type Page } from './client';

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
 * PROPOSALs too (docs/v3/05-ui-v3.md §4).
 */

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
    in_scope_pct: number;
    masked_pct: number;
    masked_note: string;
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

export const listAnswerProducts = async (status?: ProductStatus) =>
  v1Get<Page<AnswerProductSummary>>('/data-products', { layer: 'context', status, limit: 100 });

export const getAnswerProduct = async (id: string) =>
  v1Get<AnswerProduct>(`/data-products/${pathId(id)}`);

export const getProductFreshness = async (id: string) =>
  v1Get<ProductFreshness>(`/data-products/${pathId(id)}/freshness`, { periods: 8 });

export const getProductMonitoring = async (id: string) =>
  v1Get<ProductMonitoring>(`/data-products/${pathId(id)}/monitoring`);

/** A2: PATCH on an active product returns the draft version + 1; the old one serves until approved. */
export const draftProductVersion = async (id: string) =>
  v1Send<AnswerProduct>('PATCH', `/data-products/${pathId(id)}`, {});

/** A2: :submit runs the product's eval; at 95% on 10+ questions it goes to Cần bạn (BR-175). */
export const submitAnswerProduct = async (id: string, note?: string) =>
  v1Send<{ eval_run_id: string; proposal_id: string | null }>(
    'POST',
    `/data-products/${pathId(id)}:submit`,
    { note },
  );

/** A2: DELETE a member; refused for the primary of an active version (a new version is needed). */
export const removeProductMember = async (id: string, memberId: string) =>
  v1Send<void>('DELETE', `/data-products/${pathId(id)}/members/${pathId(memberId)}`);
