import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * The knowledge layer of the context service (A2 "/v1/knowledge*", UC-07-10…12): compiled pages
 * about a metric, an entity, a source or a topic, each a chain of certified versions whose claims
 * cite sources and whose figures are filled by the system at read time under the reader's
 * permissions (never written by the model, BR-162). Fields marked PROPOSAL are what the screens
 * need in a form A3 does not fix yet.
 */
export type KnowledgeKind = 'metric' | 'entity' | 'source' | 'topic';
export type KnowledgePageStatus =
  | 'draft'
  | 'pending_review'
  | 'approved'
  | 'certified'
  | 'decertified'
  | 'needs_review'
  | 'retired';

export interface KnowledgePageSummary {
  id: string;
  title: string;
  kind: KnowledgeKind;
  status: KnowledgePageStatus;
}

/** One run of the rendered summary: plain text, or a figure the system filled from a query. */
export type SummarySegment =
  | { text: string }
  | { figure: string; query_run_id: string; source_title: string };

export interface KnowledgeClaim {
  text: string;
  source_title: string;
  source_detail: string;
  valid_text: string;
  status: 'valid' | 'expired';
}

/** GET /v1/knowledge-pages/{id} — RenderedKnowledgePage. */
export interface KnowledgePage extends KnowledgePageSummary {
  version: number;
  certified_at: string | null;
  /** PROPOSAL: the certifier's name for the line under the title. */
  certified_by_name: string | null;
  /** PROPOSAL: a page citing an external source carries the "Nguồn ngoài" badge. */
  external_source: boolean;
  summary: SummarySegment[];
  claims: KnowledgeClaim[];
  /** PROPOSAL: the page's access policy in words. */
  policy: { data_spaces_text: string; classification: string; viewer_rule: string };
  links: { direction: 'out' | 'in'; title: string; page_id: string | null }[];
  version_count: number;
}

export interface KnowledgePageVersion {
  id: string;
  version: number;
  status: 'draft' | 'pending_review' | 'certified' | 'superseded';
  change_note: string | null;
  created_at: string;
}

export const listKnowledgePages = () =>
  v1Get<Page<KnowledgePageSummary>>('/knowledge-pages', { page_size: 500 });

export const getKnowledgePage = async (id: string) =>
  v1Get<KnowledgePage>(`/knowledge-pages/${pathId(id)}`);

export const listKnowledgePageVersions = async (id: string) =>
  v1Get<Page<KnowledgePageVersion>>(`/knowledge-pages/${pathId(id)}/versions`);

/**
 * A2: POST /v1/knowledge-runs (202) — recompile one page now. data_space_id is required there;
 * PROPOSAL: take it from the X-Data-Space header the client already sends, so a page can be
 * recompiled from "Toàn tổ chức" without the screen knowing the space.
 */
export const recompileKnowledgePage = (pageId: string) =>
  v1Send<{ id: string }>('POST', '/knowledge-runs', {
    kind: 'compile',
    source_kind: 'page',
    source_ref: pageId,
  });

/* ─── Rà soát tri thức (UC-07-11) ─────────────────────────────────────── */

/** The latest lint run, in the screen's terms. PROPOSAL: the counts and texts. */
export interface KnowledgeLintRun {
  id: string;
  finished_at: string;
  page_count: number;
  open_findings: number;
  high_findings: number;
  stale_pages: number;
  skipped_source_pct: number;
  duration_minutes: number;
  /** "tối đa 30 phút, 50 thẻ Cần bạn" */
  limits_text: string;
  /** "lượt dùng mô hình chạy chủ nhật 02:00" */
  schedule_text: string;
}

export type FindingKind =
  | 'contradiction'
  | 'expired_claim'
  | 'decertified_source'
  | 'orphan'
  | 'metric_without_page'
  | 'undefined_term';

export type FindingAction =
  | 'propose_edit'
  | 'mark_stale'
  | 'retire_page'
  | 'create_page'
  | 'dismiss'
  | 'reassign';

/** GET /v1/knowledge-findings — KnowledgeLintFinding. */
export interface KnowledgeFinding {
  id: string;
  severity: 'high' | 'medium' | 'low';
  kind: FindingKind;
  /** PROPOSAL: the page (or pages, term, metric) it is about, in words. */
  subject_text: string;
  detail: string;
  status: 'open' | 'resolved' | 'dismissed';
  /** PROPOSAL: the two sides of a contradiction, or other evidence lines. */
  evidence: { label: string; value: string }[];
  /** PROPOSAL: the agent's suggested action, in a sentence. */
  suggestion: string | null;
}

/** A2: GET /v1/knowledge-runs ?kind ?status — the newest finished lint run first. */
export const getLatestLintRun = () =>
  v1Get<Page<KnowledgeLintRun>>('/knowledge-runs', { kind: 'lint', status: 'succeeded' });

export const listOpenFindings = () =>
  v1Get<Page<KnowledgeFinding>>('/knowledge-findings', { status: 'open', page_size: 500 });

/** A2: one of six fixed actions; dismiss needs a reason (BR-166, hidden 90 days). */
export const resolveFinding = async (
  id: string,
  body: { action: FindingAction; reason?: string; assignee_id?: string },
) => v1Send<KnowledgeFinding>('POST', `/knowledge-findings/${pathId(id)}:resolve`, body);

/** A2: POST /v1/knowledge-runs (202) — a lint run now. */
export const runKnowledgeLint = () =>
  v1Send<{ id: string }>('POST', '/knowledge-runs', { kind: 'lint' });

/* ─── Lưu câu trả lời thành tri thức (UC-07-12) ───────────────────────── */

/**
 * PROPOSAL: GET /v1/answers/{id}/knowledge-draft — what saving this answer would propose: the
 * page it fits, the paragraph with its figures turned into placeholders bound to the query
 * (never fixed numbers, BR-162), the source and the period it holds for.
 */
interface KnowledgeDraft {
  target_page: { id: string; title: string } | null;
  paragraph: string;
  source_text: string;
  validity_text: string;
}

export const getKnowledgeDraft = async (answerId: string) =>
  v1Get<KnowledgeDraft>(`/answers/${pathId(answerId)}/knowledge-draft`);

/**
 * A2: POST /v1/knowledge-pages:from-answer — the answer becomes a draft version of the target
 * page, submitted for review; 422 business_rule_violated when a figure has no query run, a
 * citation failed its check or the answer holds personal data (BR-167).
 */
export const saveAnswerToKnowledge = (body: {
  answer_id: string;
  mode: 'paragraph' | 'page';
  target_page_id?: string;
  title?: string;
  note?: string;
}) => v1Send<KnowledgePageVersion>('POST', '/knowledge-pages:from-answer', body);
