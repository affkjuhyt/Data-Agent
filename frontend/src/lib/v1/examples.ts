import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Example questions and synonyms of the context layer, for "P07 · Câu hỏi mẫu & đồng nghĩa"
 * (2301:19194, UC-07-02). A2 has only the writes, POST /v1/examples {question, sql, metric_ids}
 * and POST /v1/synonyms {phrase, target_kind, target_id, target_value}; reading them back,
 * verifying a suggestion, editing its SQL and asking the agent for suggestions are PROPOSALs.
 */
export interface ExampleQuery {
  id: string;
  question: string;
  sql: string;
  /** PROPOSAL: what the example uses, "NPL_RATIO · vùng · tháng trước". */
  usage_text: string;
  /** PROPOSAL: who added it, or "Agent gợi ý · hỏi 7 lần". */
  source_label: string;
  status: 'verified' | 'pending';
  /** PROPOSAL, on agent suggestions: why it was suggested, and the result of running it. */
  reason: string | null;
  result_text: string | null;
}

export interface Synonym {
  id: string;
  /** "“nợ xấu”, “NPL”, “tỷ lệ nợ xấu”" — the phrases mapped to the same target. */
  phrases: string[];
  /** PROPOSAL: the target in words, "NPL_RATIO", "vùng IN (…)". */
  target_text: string;
  scope_label: string;
}

/** PROPOSAL: GET /v1/examples */
export const listExamples = () => v1Get<Page<ExampleQuery>>('/examples', { page_size: 500 });

/** PROPOSAL: GET /v1/synonyms */
export const listSynonyms = () => v1Get<Page<Synonym>>('/synonyms', { page_size: 500 });

/** A2: 422 validation_failed when the SQL does not run. */
export const addExample = (body: { question: string; sql: string }) =>
  v1Send<ExampleQuery>('POST', '/examples', body);

/** PROPOSAL: PATCH /v1/examples/{id} — the SQL is run again before it is saved. */
export const updateExampleSql = async (id: string, sql: string) =>
  v1Send<ExampleQuery>('PATCH', `/examples/${pathId(id)}`, { sql });

/** PROPOSAL: :verify marks an agent suggestion correct; it joins the standard question set. */
export const verifyExample = async (id: string) =>
  v1Send<ExampleQuery>('POST', `/examples/${pathId(id)}:verify`);

/** PROPOSAL: :suggest asks the agent for examples from real questions (202, they arrive pending). */
export const suggestExamples = () => v1Send<{ status: 'queued' }>('POST', '/examples:suggest');
