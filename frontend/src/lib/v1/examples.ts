import { type Page, api, ok } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * Example questions and synonyms of the context layer, for "P07 · Câu hỏi mẫu & đồng nghĩa"
 * (2301:19194, UC-07-02). A2: GET/POST /v1/examples, PATCH /v1/examples/{id} (the SQL is run
 * again before it is saved; confirming is status active) and GET /v1/synonyms. Suggestions come
 * from real questions reported wrong (POST /v1/feedback/{id}:to-eval) and arrive as examples of
 * origin agent_suggested or feedback; there is no "suggest now" verb.
 *
 * The screen-only fields (what an example uses in words, why the agent suggested it, the result of
 * running it) are not in ExampleView: the mock sends them under `display`, the api leaves them
 * out and the screen hides the line or shows "—".
 */
export interface ExampleQuery {
  id: string;
  question: string;
  sql: string;
  /** What the example uses, "NPL_RATIO · vùng · tháng trước"; null without it. */
  usage_text: string | null;
  /** Who added it, from the example's origin, or "Agent gợi ý · hỏi 7 lần". */
  source_label: string;
  status: 'verified' | 'pending';
  /** On agent suggestions: why it was suggested, and the result of running it. */
  reason: string | null;
  result_text: string | null;
}

export interface Synonym {
  id: string;
  /** "“nợ xấu”, “NPL”, “tỷ lệ nợ xấu”" — the phrases mapped to the same target. */
  phrases: string[];
  /** The target in words, "NPL_RATIO", "vùng IN (…)"; from A3 the kind and value only. */
  target_text: string;
  /** The Data Space or "Toàn tổ chức"; null when the api does not say (the current Data Space). */
  scope_label: string | null;
}

export type ExampleWire = Schemas['ExampleView'] & { display?: Partial<ExampleQuery> };
export type SynonymWire = Schemas['SynonymView'] & { display?: Partial<Synonym> };

/** example_query.origin: manual, agent_suggested, feedback, pack. */
const ORIGIN: Record<string, string> = {
  manual: 'Thêm thủ công',
  agent_suggested: 'Agent gợi ý',
  feedback: 'Từ câu bị báo sai',
  pack: 'Gói ngành',
};

/** synonym.target_kind: metric, dimension, measure, entity, value, column. */
const TARGET_KIND: Record<string, string> = {
  metric: 'Chỉ số',
  dimension: 'Chiều',
  measure: 'Đại lượng',
  entity: 'Thực thể',
  value: 'Giá trị',
  column: 'Cột',
};

/*
 * example_query.status: draft, pending_review, active, stale, rejected. Only an active example
 * is in the standard set; the others wait for a check or a fix.
 */
const readExample = (raw: ExampleWire): ExampleQuery => ({
  id: raw.id,
  question: raw.question,
  sql: raw.sql,
  usage_text: null,
  source_label: ORIGIN[raw.origin] ?? raw.origin,
  status: raw.status === 'active' ? 'verified' : 'pending',
  reason: null,
  result_text: null,
  ...raw.display,
});

/** One row per target: the phrases that mean the same thing are listed together. */
function readSynonyms(rows: SynonymWire[]): Synonym[] {
  const byTarget = new Map<string, Synonym>();
  for (const raw of rows) {
    if (raw.display?.phrases) {
      byTarget.set(raw.id, {
        id: raw.id,
        target_text: '',
        scope_label: null,
        phrases: [],
        ...raw.display,
      });
      continue;
    }
    const key = `${raw.target_kind}|${raw.target_id}|${raw.target_value ?? ''}`;
    const known = byTarget.get(key);
    if (known) {
      known.phrases.push(raw.phrase);
      continue;
    }
    const kind = TARGET_KIND[raw.target_kind] ?? raw.target_kind;
    byTarget.set(key, {
      id: raw.id,
      phrases: [raw.phrase],
      target_text: raw.target_value ? `${kind} · ${raw.target_value}` : kind,
      scope_label: null,
    });
  }
  return [...byTarget.values()];
}

/** A rejected example is out of the list; drafts and suggestions wait under "Chờ xem". */
export const listExamples = async () => {
  const page = await ok<Page<ExampleWire>>(
    api.GET('/v1/examples', { params: { query: { page_size: 500 } } }),
  );
  return {
    ...page,
    items: page.items.filter((e) => e.status !== 'rejected').map(readExample),
  };
};

/** Rejected synonyms are left out; a pending one already steers the agent only once approved. */
export const listSynonyms = async () => {
  const page = await ok<Page<SynonymWire>>(
    api.GET('/v1/synonyms', { params: { query: { page_size: 500 } } }),
  );
  return { ...page, items: readSynonyms(page.items.filter((s) => s.status !== 'rejected')) };
};

/** A2: 422 validation_failed when the SQL is not one read-only SELECT. */
export const addExample = async (body: { question: string; sql: string }) =>
  readExample(
    await ok<ExampleWire>(
      api.POST('/v1/examples', { body: body satisfies Schemas['ExampleCreate'] }),
    ),
  );

/** A2: PATCH /v1/examples/{id} — the SQL is run again before it is saved. */
export const updateExampleSql = async (id: string, sql: string) =>
  readExample(
    await ok<ExampleWire>(
      api.PATCH('/v1/examples/{id}', {
        params: { path: { id } },
        body: {
          sql,
        } satisfies Schemas['ExamplePatch'],
      }),
    ),
  );

/** A2: PATCH {status: active} marks a suggestion correct; it joins the standard question set. */
export const verifyExample = async (id: string) =>
  readExample(
    await ok<ExampleWire>(
      api.PATCH('/v1/examples/{id}', {
        params: { path: { id } },
        body: {
          status: 'active',
        } satisfies Schemas['ExamplePatch'],
      }),
    ),
  );
