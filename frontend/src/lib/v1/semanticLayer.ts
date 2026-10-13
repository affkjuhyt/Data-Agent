import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * The semantic layer screens of P07 (UC-07-02, UC-07-06, UC-07-09): the entity model and its
 * relationships, the business glossary with the questions the agent did not understand, and the
 * context-layer evaluation. A2: GET /v1/entities and /v1/entities/{key}, GET/POST /v1/terms,
 * GET /v1/unmapped-phrases, GET /v1/eval-sets and /v1/eval-sets/{id}, POST /v1/eval-sets/{id}/runs.
 * The screen-only fields (the diagram's layout, an entity's facts in words, a term's reading
 * fields) are not in the api's views: the mock sends them under `display` and the screens do
 * without them otherwise (docs/v3/05-ui-v3.md §4).
 */

/* ─── Mô hình thực thể ──────────────────────────────────────────────── */

export interface EntityNode {
  key: string;
  name: string;
  /** "Core · customers" */
  source_text: string;
  columns: string[];
  /** Position on the diagram, in a 0–100 grid; laid out in rows of three without `display`. */
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

/*
 * The model of one Data Space, nodes and relationships. A relationship waiting for review
 * (BR-100) is drawn but marked: the compiler does not join on it yet. Agent-inferred ones arrive
 * that way on their own; there is no "suggest now" verb.
 */
export interface EntityModel {
  title: string;
  entities: EntityNode[];
  relationships: { from: string; to: string; cardinality: string; pending: boolean }[];
  /** The entity shown on the right on first open. */
  focus: string;
}

export type EntityViewWire = components['schemas']['EntityView'] & {
  display?: Partial<EntityNode>;
};
export type EntityPageWire = Page<EntityViewWire> & {
  display?: Partial<Pick<EntityModel, 'title' | 'focus'>>;
};
export type EntityDetailWire = components['schemas']['EntityDetail'] & {
  display?: Partial<EntityDetail>;
};

/** relationship.cardinality: one_to_one, many_to_one, one_to_many — read from the left end. */
const CARDINALITY: Record<string, string> = {
  one_to_one: '1 — 1',
  many_to_one: 'n — 1',
  one_to_many: '1 — n',
};

export function readEntityModel(raw: EntityPageWire): EntityModel {
  const entities = raw.items;
  const relationships = new Map<string, EntityModel['relationships'][number]>();
  entities.forEach((entity) =>
    (entity.relationships ?? []).forEach((relation) => {
      // The other end of a table no entity is declared on is not on the diagram.
      const from = relation.from_entity ?? relation.from_dataset_id;
      const to = relation.to_entity ?? relation.to_dataset_id;
      const key = `${from}>${to}`;
      if (!relationships.has(key))
        relationships.set(key, {
          from,
          to,
          cardinality: CARDINALITY[relation.cardinality] ?? relation.cardinality,
          pending: relation.status === 'pending_review',
        });
    }),
  );
  return {
    title: raw.display?.title ?? 'Mô hình thực thể',
    entities: entities.map((entity, index) => ({
      key: entity.key,
      name: entity.name,
      source_text: entity.primary_dataset?.name ?? 'Chưa gắn bảng',
      columns: entity.key_columns,
      x: (index % 3) * 35 + 5,
      y: Math.floor(index / 3) * 28 + 8,
      ...entity.display,
    })),
    relationships: [...relationships.values()],
    focus: raw.display?.focus ?? entities[0]?.key ?? '',
  };
}

/** Without `display`: the keys, the primary table and the metrics; the api has no synonyms here. */
export function readEntityDetail(raw: EntityDetailWire): EntityDetail {
  const dimensions = (raw.dimensions ?? []).map((d) => d.name);
  const facts = [
    { label: 'Khoá', value: raw.key_columns.join(', ') || 'Chưa xác định' },
    ...(dimensions.length ? [{ label: 'Chiều', value: dimensions.join(', ') }] : []),
  ];
  return {
    key: raw.key,
    name: raw.name,
    description: raw.description ?? '',
    facts,
    sources: raw.primary_dataset
      ? [{ label: raw.primary_dataset.name, role: 'Chính', tone: 'success' }]
      : [],
    synonyms: [],
    metrics: (raw.metrics ?? []).map((metric) => metric.display_name ?? metric.code),
    more_metrics: 0,
    ...raw.display,
  };
}

export const getEntityModel = async () =>
  readEntityModel(await v1Get<EntityPageWire>('/entities', { page_size: 500 }));

export const getEntity = async (key: string) =>
  readEntityDetail(await v1Get<EntityDetailWire>(`/entities/${pathId(key)}`));

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

/** A2: GET /v1/eval-sets/{id} — the context-layer set with its runs and the latest run's misses. */
export interface ContextEval {
  id: string;
  /** The set's name; a context set is named after its Data Space. */
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

export type EvalSetWire = components['schemas']['EvalSetDetail'] & {
  display?: Partial<ContextEval>;
};
export type EvalSetRowWire = components['schemas']['EvalSetView'];

const textFrom = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (typeof value === 'object' && value !== null) {
    // An item's input is {question}, its expectation and the run's answer often {text}.
    const { question, text } = value as { question?: unknown; text?: unknown };
    if (typeof question === 'string') return question;
    if (typeof text === 'string') return text;
  }
  return value === null || value === undefined ? '—' : JSON.stringify(value);
};

const rate = (v: number | string | null | undefined) =>
  v === null || v === undefined ? null : `${Math.round(Number(v) * 1000) / 10}`.replace('.', ',');

/*
 * Adapt A3's eval-set detail. A run that adds regressions against its baseline is blocked from
 * publishing; one without is released. The change a run tried is its subject_version.
 */
export function readContextEval(raw: EvalSetWire): ContextEval {
  const runs = raw.runs ?? [];
  const latest = raw.latest_run ?? runs[0] ?? null;
  const passed = latest?.passed ?? 0;
  const failed = latest?.failed ?? 0;
  const total = latest?.item_count ?? passed + failed;
  const pct = rate(latest?.pass_rate);
  return {
    id: raw.id,
    data_space_name: raw.name,
    kpis: [
      {
        label: 'Bộ câu hỏi chuẩn',
        value: `${raw.item_count ?? total} câu`,
        note: `tối thiểu ${raw.min_items} câu`,
        tone: 'neutral',
      },
      {
        label: 'Trả lời đúng',
        value: `${passed} / ${total}`,
        note: pct === null ? '—' : `tỷ lệ ${pct}%`,
        tone: 'success',
      },
      {
        label: 'Trả lời sai',
        value: `${failed}`,
        note: 'theo lượt gần nhất',
        tone: failed ? 'error' : 'neutral',
      },
      {
        label: 'Từ chối đúng',
        value: `${latest?.correct_refusals ?? 0}`,
        note: 'thiếu quyền hoặc thiếu dữ liệu',
        tone: 'neutral',
      },
    ],
    runs: runs.map((run) => ({
      id: run.id,
      label: `#${run.id.slice(0, 8)} · ${run.started_at.slice(8, 10)}/${run.started_at.slice(5, 7)}`,
      change: run.subject_version,
      correct: run.passed,
      delta: -run.regressions,
      result:
        run.status !== 'completed' || run.regressions > 0
          ? 'blocked'
          : run.id === latest?.id
            ? 'published'
            : 'released',
    })),
    latest_run_label: latest ? `#${latest.id.slice(0, 8)}` : '—',
    misses: (raw.failures ?? []).map((failure) => ({
      question: textFrom(failure.item.input),
      expected: textFrom(failure.item.expected),
      understood: textFrom(failure.result.actual),
      cause: failure.result.cause ?? '—',
    })),
    ...raw.display,
  };
}

export const getContextEval = async (id: string) =>
  readContextEval(await v1Get<EvalSetWire>(`/eval-sets/${pathId(id)}`));

/** A2: GET /v1/eval-sets?subject_type=context — the sets of the current Data Space. */
export const listEvalSets = async () => {
  const page = await v1Get<Page<EvalSetRowWire>>('/eval-sets', {
    subject_type: 'context',
    page_size: 500,
  });
  return {
    ...page,
    items: page.items.map((item) => ({ id: item.id, data_space_name: item.name })),
  };
};

/** A2: POST /v1/eval-sets/{id}/runs (202) */
export const runContextEval = async (id: string) =>
  v1Send<{ id: string; status: string }>('POST', `/eval-sets/${pathId(id)}/runs`, {});
