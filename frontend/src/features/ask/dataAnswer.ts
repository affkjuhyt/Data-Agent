/**
 * A data answer (qa.answer kind "data") as the Ask screen draws it: the table, the chart and the
 * trust label, built from the answer's figures F1…Fn (worker-agent answer/narrate.py).
 *
 * The figures, not GET /v1/query-runs/{id}/rows, are the source: a figure whose data product is
 * overdue arrives masked (BR-178), and drawing the raw rows would show the number the text hides.
 * One figure is one result row: its dimension members and its value.
 */
import type { ReadingChip } from './interpretation';
import type { AnswerTrust } from './trust';
import type { ChartSpec, EvidenceRun } from './types';

/** One entry of qa.answer.figures as the worker writes it (Figure.as_json). */
export interface FigureWire {
  key: string;
  label: string;
  /** a decimal string, so no digit is lost (A1.1); null when masked or missing */
  value: string | null;
  unit: string | null;
  decimals: number;
  dimensions: Record<string, string>;
  display_state: 'shown' | 'masked_stale' | 'not_available';
  /** null for a figure computed from others */
  query_run_id: string | null;
  metric_version_id: string;
  trust: string;
  /** {query_run_id, row, col} for a result row; {compute_run_id, expr} for a derived figure */
  source?: { expr?: string };
}

/** qa.answer.chart_spec (answer/narrate.py chart_spec_of): the cut to draw, never the values. */
export interface ChartSpecWire {
  type: 'bar' | 'line';
  x: string;
  x_label?: string;
  series?: string;
  series_label?: string;
  y: string;
  y_label?: string;
  unit?: string | null;
  /** the metric's other allowed cuts: where a picked bar can be drilled (UC-01-04) */
  drill?: { code: string; name: string }[];
}

/** The labels the worker writes (questions.py STANDARD, NOT_STANDARD, OUT_OF_SCOPE; sqlgen/answer.py TRUST). */
const TRUST: Record<string, Omit<AnswerTrust, 'label'>> = {
  'Theo chỉ số chuẩn': {
    level: 'GOVERNED',
    tone: 'success',
    meaning:
      'Số tính từ định nghĩa chỉ số đã duyệt, trong một data product có cam kết và có biên nhận.',
  },
  'chưa có định nghĩa chuẩn': {
    level: 'EXPLORATORY',
    tone: 'warning',
    meaning: 'Chỉ số chưa được duyệt là định nghĩa chuẩn. Kiểm lại trước khi dùng để quyết định.',
  },
  'Khám phá (truy vấn do mô hình viết)': {
    level: 'EXPLORATORY',
    tone: 'warning',
    meaning:
      'Số từ truy vấn SQL do mô hình viết trên bảng của Data Space, không qua định nghĩa chỉ số đã duyệt; sáu cách viết được chạy và lấy kết quả đồng thuận. Kiểm lại trước khi dùng để quyết định.',
  },
  'Ngoài phạm vi data product, không có biên nhận': {
    level: 'EXPLORATORY',
    tone: 'warning',
    meaning:
      'Chỉ số không thuộc data product nào nên không có cam kết cập nhật và không có biên nhận.',
  },
};

const MASKED = {
  masked_stale: 'Đã ẩn: dữ liệu quá hạn cập nhật',
  not_available: 'CHƯA CÓ',
} as const;

/** The figures that are result rows. A derived one (a gap, a change, a share: worker-agent
 *  answer/insights.py) is told in the text, not drawn as one more bar or row. */
export function readFigures(raw: unknown): FigureWire[] {
  return Array.isArray(raw)
    ? raw.filter(
        (f): f is FigureWire =>
          typeof f === 'object' && f !== null && 'key' in f && !(f as FigureWire).source?.expr,
      )
    : [];
}

export function readChartSpec(raw: unknown): ChartSpecWire | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const spec = raw as Partial<ChartSpecWire>;
  return (spec.type === 'bar' || spec.type === 'line') && typeof spec.x === 'string'
    ? (spec as ChartSpecWire)
    : null;
}

/** The figure's label up to its first comma is the metric's name (narrate.py figures_of). */
function metricName(figures: FigureWire[], spec: ChartSpecWire | null): string {
  return spec?.y_label || figures[0]?.label.split(', ')[0] || 'Giá trị';
}

function dimensionLabel(code: string, spec: ChartSpecWire | null): string {
  if (spec?.x === code && spec.x_label) return spec.x_label;
  if (spec?.series === code && spec.series_label) return spec.series_label;
  return code;
}

function numberOf(f: FigureWire): number | null {
  if (f.display_state !== 'shown' || f.value === null) return null;
  const n = Number(f.value);
  return Number.isFinite(n) ? n : null;
}

/** The answer's result table: one column per dimension, then the metric. */
export function figureRun(figures: FigureWire[], spec: ChartSpecWire | null): EvidenceRun | null {
  if (!figures.length) return null;
  const metric = metricName(figures, spec);
  const codes = Object.keys(figures[0].dimensions);
  const columns = [...codes.map((c) => dimensionLabel(c, spec)), metric];
  const rows = figures.map((f) => {
    const row: Record<string, unknown> = {};
    codes.forEach((c, i) => {
      row[columns[i]] = f.dimensions[c] ?? '';
    });
    row[metric] = f.display_state === 'shown' ? numberOf(f) : MASKED[f.display_state];
    return row;
  });
  const unit = figures.find((f) => f.unit)?.unit;
  return {
    id: figures[0].query_run_id ?? undefined,
    kind: 'sql',
    sql: '',
    generated_by: 'semantic_layer',
    columns,
    column_units: unit ? { [metric]: unit } : {},
    rows,
    row_count: rows.length,
    metric: { metric, unit: unit ?? null, dimensions: codes.map((c) => dimensionLabel(c, spec)) },
  };
}

/**
 * The chart of the figures: x along the spec's cut, one line or bar group per series member.
 * A time cut is put in order (period codes sort as text: 2026-08 < 2026-09); masked figures are
 * left out, never drawn as zero. Fewer than two points draw nothing.
 */
export function figureChart(figures: FigureWire[], spec: ChartSpecWire | null): ChartSpec | null {
  if (!spec) return null;
  const metric = metricName(figures, spec);
  const xKey = spec.x_label || spec.x;
  const byX = new Map<string, Record<string, unknown>>();
  const yKeys: string[] = [];
  for (const f of figures) {
    const value = numberOf(f);
    if (value === null) continue;
    const x = f.dimensions[spec.x] ?? '';
    const y = spec.series ? (f.dimensions[spec.series] ?? '') : metric;
    if (!yKeys.includes(y)) yKeys.push(y);
    const point = byX.get(x) ?? { [xKey]: x };
    point[y] = value;
    byX.set(x, point);
  }
  const data = [...byX.values()];
  if (data.length < 2) return null;
  if (spec.type === 'line') data.sort((a, b) => String(a[xKey]).localeCompare(String(b[xKey])));
  const unit = spec.unit ? ` (${spec.unit})` : '';
  return {
    chart_type: spec.type,
    title: `${metric}${unit} theo ${xKey}`,
    x_key: xKey,
    y_keys: yKeys,
    data,
  };
}

/** The label the worker gave the figures; all figures of an answer carry the same one. */
export function figureTrust(figures: FigureWire[]): AnswerTrust | null {
  const label = figures[0]?.trust;
  const known = label ? TRUST[label] : undefined;
  return label && known
    ? { label: label.charAt(0).toUpperCase() + label.slice(1), ...known }
    : null;
}

/** Where a picked bar of the chart can be drilled: its axis and the other cuts (UC-01-04). */
export interface Drill {
  axis: string;
  cuts: { code: string; name: string }[];
}

export function figureDrill(spec: ChartSpecWire | null): Drill | null {
  if (!spec?.drill?.length || spec.type !== 'bar' || spec.series) return null;
  return { axis: spec.x_label || spec.x, cuts: spec.drill };
}

/** The follow-up a drill sends; the worker reads it against this question (resolver follow_up). */
export const drillQuestion = (member: string, cut: string) => `Đi sâu ${member} theo ${cut}`;

/** A step of the plan waiting for the person: the decomposition skill of a "vì sao" question. */
export interface DecompositionProposal {
  explain: string;
  params: { metric: string; dimension: string; period: string; base_period: string };
}

const CHIP_LABEL: Record<string, string> = {
  metric: 'Chỉ số',
  dimension: 'Chiều',
  filter: 'Phạm vi',
  period: 'Kỳ',
};

interface PlanWire {
  chips?: unknown;
  steps?: unknown;
}

/** "Hệ thống hiểu là" from the plan's chips, an inherited chip marked as such (AC-01-04-01, 07:
 *  one chip per filter), and the decomposition the plan proposes. */
export function readPlan(plan: PlanWire | null | undefined): {
  reading: ReadingChip[];
  proposal: DecompositionProposal | null;
} {
  const chips = Array.isArray(plan?.chips) ? (plan.chips as Record<string, unknown>[]) : [];
  const reading = chips
    .filter((c) => typeof c.kind === 'string' && c.kind in CHIP_LABEL)
    .map((c, i) => ({
      key: `${String(c.kind)}-${i}`,
      label: CHIP_LABEL[String(c.kind)],
      value: String(c.label ?? c.value ?? ''),
      inherited: c.source === 'inherited',
    }));
  const ir = (plan?.steps ?? {}) as { steps?: unknown };
  const steps = Array.isArray(ir.steps) ? (ir.steps as Record<string, unknown>[]) : [];
  const step = steps.find((s) => s.kind === 'skill' && s.checkpoint === 'confirm');
  const args = (step?.args ?? {}) as { category?: string; params?: Record<string, unknown> };
  const p = args.params ?? {};
  const proposal =
    step &&
    args.category === 'decomposition' &&
    typeof p.metric === 'string' &&
    typeof p.period === 'string'
      ? {
          explain: String(step.explain ?? 'Phân rã biến động'),
          params: {
            metric: p.metric,
            dimension: String(p.dimension ?? ''),
            period: p.period,
            base_period: String(p.base_period ?? ''),
          },
        }
      : null;
  return { reading, proposal };
}
