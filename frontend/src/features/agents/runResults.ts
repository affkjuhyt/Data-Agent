/**
 * What a run found, as the run screen shows it under the story: each answered question of the
 * goal (worker-agent runs.py, one query step per line of a report goal) with its sentence and its
 * chart. The step's output keeps the columns, the first rows (at most 50) and the chart_spec the
 * Ask screen uses (narrate.py chart_spec_of): bars along a member, a line along time, a second cut
 * as series.
 */
import type { ChartSpec } from '../ask/types';

interface StepChartSpec {
  type: string;
  x: string;
  x_label?: string;
  y_label?: string;
  unit?: string | null;
  series?: string;
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

function specOf(raw: unknown): StepChartSpec | null {
  if (!isRecord(raw)) return null;
  return (raw.type === 'bar' || raw.type === 'line') && typeof raw.x === 'string'
    ? (raw as unknown as StepChartSpec)
    : null;
}

function numberOf(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** The step's rows drawn along its chart_spec; null when there is nothing to compare. */
export function stepChart(output: unknown): ChartSpec | null {
  if (!isRecord(output)) return null;
  const spec = specOf(output.chart_spec);
  const columns = Array.isArray(output.columns) ? output.columns.map(String) : [];
  const rows = Array.isArray(output.rows) ? output.rows.filter(Array.isArray) : [];
  if (!spec) return null;
  const xAt = columns.indexOf(spec.x);
  const valueAt = columns.indexOf('value');
  const seriesAt = spec.series ? columns.indexOf(spec.series) : -1;
  if (xAt < 0 || valueAt < 0) return null;
  const metric = spec.y_label || 'Giá trị';
  const xKey = spec.x_label || spec.x;
  const byX = new Map<string, Record<string, unknown>>();
  const yKeys: string[] = [];
  for (const row of rows as unknown[][]) {
    const value = numberOf(row[valueAt]);
    if (value === null) continue;
    const x = String(row[xAt] ?? '');
    const y = seriesAt >= 0 ? String(row[seriesAt] ?? '') : metric;
    if (!yKeys.includes(y)) yKeys.push(y);
    const point = byX.get(x) ?? { [xKey]: x };
    point[y] = value;
    byX.set(x, point);
  }
  const data = [...byX.values()];
  if (data.length < 2) return null;
  // Period codes sort as text (2026-04-30 < 2026-09-30).
  if (spec.type === 'line') data.sort((a, b) => String(a[xKey]).localeCompare(String(b[xKey])));
  const unit = spec.unit ? ` (${spec.unit})` : '';
  return {
    chart_type: spec.type as ChartSpec['chart_type'],
    title: `${metric}${unit} theo ${xKey}`,
    x_key: xKey,
    y_keys: yKeys,
    data,
  };
}

/** The question a query step answered (the line of the goal it was planned from). */
export function stepQuestion(output: unknown): string | null {
  return isRecord(output) && typeof output.question === 'string' ? output.question : null;
}
