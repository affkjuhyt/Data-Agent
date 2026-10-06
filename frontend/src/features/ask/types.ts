/**
 * Wire types for one answer from the orchestrator SSE stream
 * (backend/services/workflow_runtime/orchestrator/routers/stream.py) and the frames the
 * workflow_runtime forwards through it (runtime_services/conversation_runner.py).
 *
 * Nothing here is invented client-side: every field maps to something the
 * backend already writes into state — the point of the Ask screen is to show
 * what exists, not to add a second source of truth.
 */

export interface ChartSpec {
  chart_type: 'bar' | 'line' | 'area' | 'pie' | 'scatter' | 'radar' | 'radialBar' | 'composed';
  title: string;
  x_key: string;
  y_keys: string[];
  data: Record<string, unknown>[];
  /** only for chart_type === 'composed' */
  series?: Array<{ key: string; type: 'bar' | 'line' | 'area' }>;
}

export type CitationType = 'kb' | 'mcp' | 'db' | 'web' | 'tool';

export interface Citation {
  index: number;
  source: string;
  type: CitationType;
  score?: number | null;
  snippet: string;
  url?: string | null;
  doc_id?: string | null;
  provenance?: DataProvenance | null;
  truncation?: Truncation | null;
  aggregation?: AggregationReport | null;
}

/** backend/libs/semantic — how the number was DEFINED (approved metric contract). */
export interface MetricProvenance {
  /** "doanh_thu_thuan@3" — key@version */
  metric: string;
  matched_term?: string;
  definition?: string;
  aggregation?: string;
  unit?: string | null;
  grain?: string | null;
  dimensions?: string[];
  filters?: unknown[];
  business_date_column?: string | null;
  owner?: string | null;
}

/** backend/libs/shared/freshness.TableGovernance.to_dict() */
interface TableGovernance {
  table: string;
  owner?: string | null;
  classification?: string | null;
  upstream?: string[];
  data_as_of?: string | null;
  freshness_column?: string | null;
  freshness_checked_at?: string | null;
  freshness_sla_seconds?: number | null;
  last_crawled_at?: string | null;
}

/** backend/libs/shared/freshness.Provenance.to_dict() — how CURRENT the data is. */
export interface DataProvenance {
  status: 'fresh' | 'stale' | 'unknown' | string;
  as_of?: string | null;
  age_seconds?: number | null;
  classification?: string | null;
  owners?: string[];
  stale_tables?: string[];
  unknown_tables?: string[];
  lineage?: unknown;
  tables?: TableGovernance[];
}

/** handlers/data.py — result was cut by the sandbox caps. */
export interface Truncation {
  reason: 'row_limit' | 'byte_limit' | 'cell_limit' | string;
  rows_returned?: number;
  rows_total?: number | null;
  result_bytes?: number | null;
  cells_truncated?: number;
}

export interface ResultColumn {
  name: string;
  type: 'text' | 'number' | 'date' | 'datetime' | 'bool' | 'json' | string;
  unit?: string | null;
}

export interface ResultColumnStats {
  nulls: number;
  min?: string | number | null;
  max?: string | number | null;
  sum?: string | number | null;
  distinct?: string[] | null;
  distinct_count?: number | null;
}

/** First-class query result sent in one `__result_set` frame. */
export interface ResultSet {
  result_id: string;
  kind: 'table' | 'scalar' | 'series' | string;
  columns: ResultColumn[];
  row_count: number;
  rows_preview: Record<string, unknown>[];
  stats: Record<string, ResultColumnStats>;
  provenance: {
    connection_id: string;
    engine: string;
    sql?: string | null;
    sql_hash?: string | null;
    tables: string[];
    executed_at: string;
    watermark?: string | null;
    truncation?: Truncation | null;
    generated_by: 'semantic_layer' | 'verified_query' | 'llm' | string;
    metric?: MetricProvenance | null;
    freshness?: DataProvenance | null;
    aggregation?: AggregationReport | null;
  };
  trust: 'VERIFIED' | 'GOVERNED' | 'EXPLORATORY' | string;
  warnings: Array<{ code: string; detail: string }>;
  full_rows_available: boolean;
  citation_index?: number | null;
}

/** backend/libs/shared/aggregation_check.AggregationReport.to_dict() */
export interface AggregationReport {
  findings: Array<{ kind: string; detail: string; expression?: string }>;
  structurally_safe: boolean;
  aggregates: string[];
  joins: number;
}

/** One SQL execution the answer was built from — `__evidence.runs[]`. */
export interface EvidenceRun {
  id?: string | null;
  kind: 'sql' | string;
  db_name?: string | null;
  db_type?: string | null;
  sql: string;
  sql_hash?: string | null;
  executed_at?: string | null;
  watermark?: string | null;
  row_count?: number | null;
  /** "semantic_layer" = compiled from an approved metric; "llm" = model-written. */
  generated_by: 'semantic_layer' | 'llm' | string;
  trust?: 'VERIFIED' | 'GOVERNED' | 'EXPLORATORY' | string;
  metric?: MetricProvenance | null;
  provenance?: DataProvenance | null;
  truncation?: Truncation | null;
  aggregation?: AggregationReport | null;
  columns: string[];
  column_units?: Record<string, string>;
  rows: Record<string, unknown>[];
  rows_capped?: boolean;
  result_warnings?: Array<{ code: string; detail: string }>;
  stats?: Record<string, ResultColumnStats>;
  export?: {
    result_id: string;
    preview_rows: number;
    total_rows: number;
    full_available: boolean;
    xlsx_available: boolean;
    file_name?: string;
    sheets?: string[];
  };
  /** which [n] marker in the answer text this run backs */
  citation_index?: number;
}

/** backend/libs/semantic/ambiguity.Question.to_dict() */
export interface ClarificationQuestion {
  reason?: 'missing_time_range' | 'ambiguous_metric' | 'comparison_without_baseline' | string;
  text: string;
  options?: string[];
}

export interface Evidence {
  runs: EvidenceRun[];
  clarifications: ClarificationQuestion[];
  /** kind "assumption": a default reading applied instead of asking again (carries `reason`) */
  warnings: Array<{ kind: string; detail: string; expression?: string; reason?: string }>;
}

/**
 * Why a turn ended without an answer — the `__refusal` frame, shape of
 * backend/libs/contracts/v1/errors.py `Refusal`. Screens branch on `code` and
 * `next_action`, never on `message` (which is the user-facing sentence).
 */
export type RefusalNextAction = 'retry' | 'rephrase' | 'contact_steward';

export interface Refusal {
  /** ErrorCode, e.g. AGENT_CONFIG_ERROR, PLANNER_ERROR — an open set */
  code: string;
  message: string;
  /** an unknown value (a newer backend) renders as no action, never as a wrong one */
  next_action: RefusalNextAction | string;
  /** finds the turn's trace for whoever operates the platform */
  detail_id?: string;
  /** checks that failed, for a plan refused before it ran (R0-10) */
  issues?: string[];
}

/**
 * Two ways the backend asks before answering:
 *  - `resume`: the orchestrator graph is interrupted; the answer must go to
 *    POST /api/orchestrate/resume/stream with the session id.
 *  - `followup`: the agent answered with a question (deterministic rules in
 *    backend/libs/semantic/ambiguity.py, or a responder <OPTIONS> block); the answer
 *    is simply the next user turn and the backend merges it into the slots.
 */
export type Clarification =
  | { kind: 'resume'; sessionId: string; questions: ClarificationQuestion[] }
  /** `query`: the question as sent on the turn that asked, so the answer can
   *  be sent as that question plus the answer instead of the bare answer. */
  | { kind: 'followup'; questions: ClarificationQuestion[]; query?: string };

/** An option picked on the card — backend workflow_runtime.schemas.ClarificationAnswer. */
export interface ClarificationAnswer {
  reason: string;
  value: string;
}

/** What answering a clarification card produces. `answers` is empty for a
 *  free-text reply or options that carry no reason. */
export interface ClarificationReply {
  text: string;
  answers: ClarificationAnswer[];
}

export interface PendingReviewInfo {
  convId: string;
  payload: { title?: string; description?: string; detail?: unknown } | null;
}

export interface AskMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** ISO timestamp */
  timestamp: string;
  status?: 'streaming' | 'done' | 'error';
  sessionId?: string;
  suggestions?: string[];
  chartSpecs?: ChartSpec[];
  resultSets?: ResultSet[];
  citations?: Citation[];
  evidence?: Evidence;
  clarification?: Clarification;
  pendingReview?: PendingReviewInfo;
  /** set instead of an answer: the turn was refused with a code */
  refusal?: Refusal;
  latencyMs?: number;
}

export interface AgentOption {
  id: string;
  label: string;
  description: string;
  routing_keywords?: string[];
}
