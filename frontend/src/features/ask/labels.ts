/**
 * English labels for the closed vocabularies the backend sends.
 *
 * Slot keys mirror backend/libs/semantic/slots.py (RELATIVE_PERIODS, COMPARE_*),
 * reasons mirror backend/libs/semantic/ambiguity.Reason. Anything unknown falls back
 * to the raw value so a new backend key is still visible, never blank.
 */

const OPTION_LABELS: Record<string, string> = {
  this_month: 'This month',
  last_month: 'Last month',
  this_quarter: 'This quarter',
  last_quarter: 'Last quarter',
  this_year: 'This year',
  last_year: 'Last year',
  this_week: 'This week',
  last_week: 'Last week',
  today: 'Today',
  yesterday: 'Yesterday',
  previous_period: 'Previous period',
  same_period_last_year: 'Same period last year',
};

const REASON_LABELS: Record<string, string> = {
  missing_time_range: 'Missing time range',
  ambiguous_metric: 'Ambiguous metric',
  comparison_without_baseline: 'Missing comparison baseline',
};

export const FRESHNESS_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  fresh: { label: 'Fresh data', tone: 'ok' },
  stale: { label: 'Stale data — overdue for a refresh', tone: 'bad' },
  unknown: { label: 'Freshness not measured', tone: 'warn' },
};

export const TRUNCATION_LABELS: Record<string, string> = {
  row_limit: 'only the first rows are returned (row cap)',
  byte_limit: 'only the first rows are returned, also hit the size cap',
  cell_limit: 'some values were truncated',
};

export const GENERATED_BY_LABELS: Record<string, string> = {
  semantic_layer: 'Approved metric',
  verified_query: 'Verified query',
  llm: 'Model-generated SQL',
};

export const CITATION_TAGS: Record<string, string> = {
  kb: 'Document', web: 'Web', mcp: 'MCP', tool: 'Tool', db: 'Database',
};

export const optionLabel = (key: string): string => OPTION_LABELS[key] ?? key;
export const reasonLabel = (key?: string): string => (key ? REASON_LABELS[key] ?? key : '');
