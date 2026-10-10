import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * The semantic layer screens of P07 (UC-07-02, UC-07-06, UC-07-09): the entity model and its
 * relationships, the business glossary with the questions the agent did not understand, and the
 * context-layer evaluation. A2 has POST /v1/relationships, GET/POST /v1/terms, eval-set items and
 * runs, and GET /v1/eval-runs/{id}; reading the model, a term's meanings, the unknown questions,
 * relationship suggestions and the eval overview are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

/* ─── Mô hình thực thể ──────────────────────────────────────────────── */

export interface EntityNode {
  key: string;
  name: string;
  /** "Core · customers" */
  source_text: string;
  columns: string[];
  /** Position on the diagram, in a 0–100 grid; the server lays it out. */
  x: number;
  y: number;
}

export interface EntityDetail {
  key: string;
  name: string;
  description: string;
  facts: { label: string; value: string }[];
  sources: { label: string; role: string; tone: 'success' | 'info' | 'neutral' }[];
  synonyms: string[];
  metrics: string[];
  more_metrics: number;
}

/** PROPOSAL: GET /v1/entities — the model of one Data Space, nodes and relationships. */
export interface EntityModel {
  title: string;
  entities: EntityNode[];
  relationships: { from: string; to: string; cardinality: string }[];
  /** The entity shown on the right on first open. */
  focus: string;
}

export const getEntityModel = async () => v1Get<EntityModel>('/entities');

/** PROPOSAL: GET /v1/entities/{key} */
export const getEntity = async (key: string) => v1Get<EntityDetail>(`/entities/${pathId(key)}`);

/** PROPOSAL: POST /v1/entities:suggest-relationships — the agent's candidates, not yet saved. */
export interface RelationshipSuggestion {
  id: string;
  from_label: string;
  to_label: string;
  arrow: '→' | '↔';
  match_pct: number;
  needs_owner: string | null;
  /** What POST /v1/relationships takes to save it (A2). */
  body: {
    from_dataset_id: string;
    from_columns: string[];
    to_dataset_id: string;
    to_columns: string[];
    cardinality: string;
  };
}

export const suggestRelationships = async () =>
  v1Send<{ items: RelationshipSuggestion[]; note: string | null }>(
    'POST',
    '/entities:suggest-relationships',
  );

/** A2: POST /v1/relationships — created pending_review (BR-100). */
export const createRelationship = async (body: RelationshipSuggestion['body']) =>
  v1Send<{ id: string; status: string }>('POST', '/relationships', body);

/* ─── Từ điển nghiệp vụ ─────────────────────────────────────────────── */

/** A2: GlossaryTerm; `view=glossary` asks for the reading shape (PROPOSAL). */
export interface GlossaryEntry {
  id: string;
  term: string;
  maps_to: string;
  synonyms: string;
  data_space_text: string;
  status: 'certified' | 'ambiguous' | 'draft';
  meanings: { label: string; expression: string; default: boolean }[];
  ambiguity_rule: string | null;
  owner_name: string;
  usage_text: string;
}

export const listGlossary = async () =>
  v1Get<Page<GlossaryEntry>>('/terms', { view: 'glossary', limit: 200 });

/** PROPOSAL: GET /v1/terms/unknown — phrases the agent did not understand in 30 days. */
export interface UnknownPhrase {
  id: string;
  phrase: string;
  count: number;
  suggestion: string;
  /** What "Thêm" sends to POST /v1/terms. */
  draft: { term: string; definition: string };
}

export const listUnknownPhrases = async () => v1Get<Page<UnknownPhrase>>('/terms/unknown');

/** A2: POST /v1/terms */
export const createTerm = async (body: { term: string; definition: string }) =>
  v1Send<GlossaryEntry>('POST', '/terms', body);

/* ─── Đánh giá lớp ngữ cảnh ─────────────────────────────────────────── */

/** PROPOSAL: GET /v1/eval-sets/{id} — the context-layer set with its runs and the latest run's misses. */
export interface ContextEval {
  id: string;
  data_space_name: string;
  kpis: { label: string; value: string; note: string; tone: 'success' | 'error' | 'neutral' }[];
  runs: {
    id: string;
    label: string;
    change: string;
    correct: number;
    delta: number;
    result: 'published' | 'released' | 'blocked';
  }[];
  latest_run_label: string;
  misses: { question: string; expected: string; understood: string; cause: string }[];
}

export const getContextEval = async (id: string) => v1Get<ContextEval>(`/eval-sets/${pathId(id)}`);

export const listEvalSets = async () =>
  v1Get<Page<{ id: string; data_space_name: string }>>('/eval-sets', { kind: 'context' });

/** A2: POST /v1/eval-sets/{id}/runs (202) */
export const runContextEval = async (id: string) =>
  v1Send<{ id: string }>('POST', `/eval-sets/${pathId(id)}/runs`, {});
