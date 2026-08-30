/**
 * Wire types for one answer from the orchestrator SSE stream
 * (services/orchestrator/routers/stream.py) and the frames the
 * workflow-runtime forwards through it (runtime_services/conversation_runner.py).
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

/** libs/semantic — how the number was DEFINED (approved metric contract). */
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

/** libs/shared/freshness.TableGovernance.to_dict() */
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

/** libs/shared/freshness.Provenance.to_dict() — how CURRENT the data is. */
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
  cells_truncated?: number;
}

/** libs/shared/aggregation_check.AggregationReport.to_dict() */
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
  row_count?: number | null;
  /** "semantic_layer" = compiled from an approved metric; "llm" = model-written. */
  generated_by: 'semantic_layer' | 'llm' | string;
  metric?: MetricProvenance | null;
  provenance?: DataProvenance | null;
  truncation?: Truncation | null;
  aggregation?: AggregationReport | null;
  columns: string[];
  rows: Record<string, unknown>[];
  rows_capped?: boolean;
  /** which [n] marker in the answer text this run backs */
  citation_index?: number;
}

/** libs/semantic/ambiguity.Question.to_dict() */
export interface ClarificationQuestion {
  reason?: 'missing_time_range' | 'ambiguous_metric' | 'comparison_without_baseline' | string;
  text: string;
  options?: string[];
}

export interface Evidence {
  runs: EvidenceRun[];
  clarifications: ClarificationQuestion[];
  warnings: Array<{ kind: string; detail: string; expression?: string }>;
}

/**
 * Two ways the backend asks before answering:
 *  - `resume`: the orchestrator graph is interrupted; the answer must go to
 *    POST /api/orchestrate/resume with the session id.
 *  - `followup`: the agent answered with a question (deterministic rules in
 *    libs/semantic/ambiguity.py, or a responder <OPTIONS> block); the answer
 *    is simply the next user turn and the backend merges it into the slots.
 */
export type Clarification =
  | { kind: 'resume'; sessionId: string; questions: ClarificationQuestion[] }
  | { kind: 'followup'; questions: ClarificationQuestion[] };

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
  citations?: Citation[];
  evidence?: Evidence;
  clarification?: Clarification;
  pendingReview?: PendingReviewInfo;
  latencyMs?: number;
}

export interface AgentOption {
  id: string;
  label: string;
  description: string;
  routing_keywords?: string[];
}

/** OrchestrateResponse from POST /api/orchestrate/resume */
export interface OrchestrateResponse {
  plan_id: string;
  response: string;
  success: boolean;
  needs_clarification?: boolean;
  clarification_questions?: string[];
  suggestions?: string[];
  session_id?: string;
}
