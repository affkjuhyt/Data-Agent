import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

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

export type EntityViewWire = components['schemas']['EntityView'];
export type EntityDetailWire = components['schemas']['EntityDetail'];

export function readEntityModel(raw: Page<EntityViewWire>): EntityModel {
  if ('entities' in raw) return raw as unknown as EntityModel;
  const entities = raw.items ?? [];
  const relationships = new Map<string, { from: string; to: string; cardinality: string }>();
  entities.forEach((entity) =>
    (entity.relationships ?? []).forEach((relation) => {
      const from = relation.from_entity ?? entity.key;
      const to = relation.to_entity ?? relation.to_dataset_id;
      const key = `${from}>${to}`;
      if (!relationships.has(key)) relationships.set(key, { from, to, cardinality: relation.cardinality });
    }),
  );
  return {
    title: 'Mô hình thực thể',
    entities: entities.map((entity, index) => ({
      key: entity.key,
      name: entity.name,
      source_text: entity.primary_dataset?.name ?? 'Chưa gắn bảng',
      columns: entity.key_columns,
      x: entities.length ? (index % 3) * 35 + 5 : 5,
      y: entities.length ? Math.floor(index / 3) * 28 + 8 : 8,
    })),
    relationships: [...relationships.values()],
    focus: entities[0]?.key ?? '',
  };
}

export function readEntityDetail(raw: EntityDetailWire): EntityDetail {
  if ('facts' in raw) return raw as unknown as EntityDetail;
  const source = raw.primary_dataset?.name ?? 'Chưa gắn bảng';
  return {
    key: raw.key,
    name: raw.name,
    description: raw.description ?? '',
    facts: [{ label: 'Khóa chính', value: raw.key_columns.join(', ') || 'Chưa xác định' }],
    sources: [{ label: source, role: 'Bảng chính', tone: 'success' }],
    synonyms: [],
    metrics: (raw.metrics ?? []).map((metric) => metric.display_name ?? metric.code),
    more_metrics: 0,
  };
}

export const getEntityModel = async () => {
  const raw = await v1Get<Page<EntityViewWire>>('/entities');
  return readEntityModel(raw);
};

export const getEntity = async (key: string) => {
  const raw = await v1Get<EntityDetailWire>(`/entities/${pathId(key)}`);
  return readEntityDetail(raw);
};

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

/** GET /v1/terms as the api sends it (TermView), with the PROPOSAL reading fields. */
export type GlossaryWire = components['schemas']['TermView'] &
  Partial<
    Pick<
      GlossaryEntry,
      'maps_to' | 'synonyms' | 'data_space_text' | 'ambiguity_rule' | 'owner_name' | 'usage_text'
    >
  >;

/** `meanings` is jsonb: the entries with a label are kept, `default_meaning` marks one. */
const readMeanings = (raw: GlossaryWire): GlossaryEntry['meanings'] =>
  (Array.isArray(raw.meanings) ? raw.meanings : []).flatMap((m: unknown, i: number) => {
    if (typeof m !== 'object' || m === null) return [];
    const { label, expression } = m as { label?: unknown; expression?: unknown };
    if (typeof label !== 'string') return [];
    return [
      {
        label,
        expression: typeof expression === 'string' ? expression : '',
        default: raw.default_meaning === i,
      },
    ];
  });

/** An active term with more than one meaning is ambiguous; one not yet approved is a draft. */
function readGlossary(raw: GlossaryWire): GlossaryEntry {
  const meanings = readMeanings(raw);
  return {
    id: raw.id,
    term: raw.term,
    maps_to: raw.maps_to ?? raw.definition,
    synonyms: raw.synonyms ?? '',
    data_space_text: raw.data_space_text ?? '',
    status: raw.status !== 'active' ? 'draft' : meanings.length > 1 ? 'ambiguous' : 'certified',
    meanings,
    ambiguity_rule: raw.ambiguity_rule ?? null,
    owner_name: raw.owner_name ?? '',
    usage_text: raw.usage_text ?? '',
  };
}

/** `view=glossary` (PROPOSAL) asks for the reading fields; the api sends TermView. */
export const listGlossary = async () => {
  const page = await v1Get<Page<GlossaryWire>>('/terms', { view: 'glossary', page_size: 500 });
  return { ...page, items: page.items.map(readGlossary) };
};

/** GET /v1/unmapped-phrases: what the agent did not understand, the most asked first (UC-07-09). */
export interface UnknownPhrase {
  id: string;
  phrase: string;
  count: number;
  suggestion: string;
  /** What "Thêm" sends to POST /v1/terms. */
  draft: { term: string; definition: string };
}

/** PROPOSAL: the suggestion in words and the draft "Thêm" sends; the mock sends them. */
export type UnknownPhraseWire = components['schemas']['UnmappedPhraseView'] &
  Partial<Pick<UnknownPhrase, 'suggestion' | 'draft'>>;

/** suggested_target is jsonb: a sentence, or the target's label or code. */
const suggestionOf = (target: unknown): string => {
  if (typeof target === 'string') return target;
  if (typeof target !== 'object' || target === null) return '';
  const { label, code } = target as { label?: unknown; code?: unknown };
  const text = typeof label === 'string' ? label : typeof code === 'string' ? code : '';
  return text ? `Đề xuất: ${text}` : '';
};

const readUnknown = (raw: UnknownPhraseWire): UnknownPhrase => ({
  id: raw.id,
  phrase: raw.phrase_norm,
  count: raw.ask_count,
  suggestion: raw.suggestion ?? suggestionOf(raw.suggested_target),
  draft: raw.draft ?? { term: raw.phrase_norm, definition: '' },
});

export const listUnknownPhrases = async () => {
  const page = await v1Get<Page<UnknownPhraseWire>>('/unmapped-phrases', { page_size: 500 });
  return { ...page, items: page.items.map(readUnknown) };
};

/** A2: POST /v1/terms — a new term waits for review (pending_review). */
export const createTerm = async (body: { term: string; definition: string }) =>
  readGlossary(await v1Send<GlossaryWire>('POST', '/terms', body));

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

export type EvalSetWire = components['schemas']['EvalSetDetail'];

/** Adapt A3's eval-set detail into the context evaluation screen's presentation model. */
export function readContextEval(raw: EvalSetWire): ContextEval {
  if ('data_space_name' in raw || 'kpis' in raw) return raw as unknown as ContextEval;
  const runs = raw.runs ?? [];
  const latest = raw.latest_run ?? runs[0];
  const passed = latest?.passed ?? 0;
  const failed = latest?.failed ?? 0;
  const total = latest?.item_count ?? passed + failed;
  return {
    id: raw.id,
    data_space_name: raw.name,
    kpis: [
      { label: 'Bộ câu hỏi chuẩn', value: `${raw.item_count} câu`, note: raw.subject_type, tone: 'neutral' },
      { label: 'Trả lời đúng', value: `${passed} / ${total}`, note: `Tỷ lệ ${latest?.pass_rate ?? 0}%`, tone: 'success' },
      { label: 'Trả lời sai', value: `${failed}`, note: 'Theo lượt gần nhất', tone: failed ? 'error' : 'neutral' },
      { label: 'Từ chối đúng', value: `${latest?.correct_refusals ?? 0}`, note: 'Thiếu quyền hoặc dữ liệu', tone: 'neutral' },
    ],
    runs: runs.map((run) => ({
      id: run.id,
      label: `#${run.id.slice(0, 8)}`,
      change: run.subject_version,
      correct: run.passed,
      delta: run.baseline_run_id ? run.regressions * -1 : 0,
      result: run.status === 'completed' && run.regressions === 0 ? 'published' : run.status === 'completed' ? 'released' : 'blocked',
    })),
    latest_run_label: latest ? `#${latest.id.slice(0, 8)}` : '—',
    misses: (raw.failures ?? []).map((failure) => ({
      question: textFrom(failure.item.input),
      expected: textFrom(failure.item.expected),
      understood: textFrom(failure.result.actual),
      cause: failure.result.cause ?? 'Không có mô tả',
    })),
  };
}

const textFrom = (value: unknown) =>
  typeof value === 'string' ? value : JSON.stringify(value) ?? '—';

export const getContextEval = async (id: string) => {
  const raw = await v1Get<EvalSetWire>(`/eval-sets/${pathId(id)}`);
  return readContextEval(raw);
};

export const listEvalSets = async () =>
  v1Get<Page<EvalSetWire>>('/eval-sets', { subject_type: 'context' }).then((page) => ({
    ...page,
    items: page.items.map((item) => ({ id: item.id, data_space_name: item.name })),
  }));

/** A2: POST /v1/eval-sets/{id}/runs (202) */
export const runContextEval = async (id: string) =>
  v1Send<{ id: string }>('POST', `/eval-sets/${pathId(id)}/runs`, {});
