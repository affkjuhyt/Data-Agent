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
