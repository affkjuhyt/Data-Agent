import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Segmented } from '../../design-system/Segmented';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import type { ExploreResult, ExploreRow } from '../../lib/v1/explore';
import { cn } from '../../lib/utils';
import { formatDelta, formatValue } from './format';

/*
 * The chart card of "v3.1 · Khám phá" (2140:15648): title, the metric version and timing, the
 * Cột / Đường / Bảng switch (it changes the drawing, never the data), the legend, a 200px plot
 * with the category labels under it, and the links under the chart. The first series is
 * chart-6 and the last chart-1, as the frame compares an earlier period with the latest.
 * While a new query runs, the bars of the last result turn subtle ("Khám phá · Đang tải").
 * A click on a column group drills into it when the result names a drill dimension; the same
 * drill is offered as buttons for the keyboard and screen readers, which an SVG bar is not.
 *
 * Drawn by recharts, as the answer's charts are (ChartRenderer): the hand-drawn bars had no value
 * axis and shrank a negative value to a 2px stub on the baseline.
 */
export type ChartKind = 'bar' | 'line' | 'table';

const PLOT = 200;

function seriesColor(index: number, count: number): string {
  if (index === count - 1) return 'bg-chart-1';
  return ['bg-chart-6', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5'][index % 5];
}

/** The CSS colour of a series, as seriesColor's class names it (the legend draws those). */
const seriesFill = (index: number, count: number) =>
  `var(--${seriesColor(index, count).replace('bg-', '')})`;

/** recharts' rows: one record per category, each series' value under its key. */
export function chartRows(result: ExploreResult): Record<string, string | number | null>[] {
  return result.rows.map((row) => ({
    key: row.key,
    label: row.short_label,
    ...Object.fromEntries(result.series.map((s, i) => [s.key, row.values[i] ?? null])),
  }));
}

const TOOLTIP: React.CSSProperties = {
  background: 'var(--card)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  boxShadow: 'var(--shadow-md)',
  fontSize: 12,
  color: 'var(--foreground)',
};

export function ResultChart({
  result,
  kind,
  onKind,
  loading,
  onDrill,
  links,
}: {
  result: ExploreResult;
  kind: ChartKind;
  onKind: (kind: ChartKind) => void;
  loading: boolean;
  onDrill?: (row: ExploreRow) => void;
  links: React.ReactNode;
}) {
  const n = result.series.length;
  const data = chartRows(result);
  const negative = result.rows.some((r) => r.values.some((x) => (x ?? 0) < 0));
  const drill = onDrill && !loading ? onDrill : undefined;
  const describe = (row: ExploreRow) =>
    row.values
      .map((x, i) => `${result.series[i]?.label}: ${formatValue(x, result.unit)}`)
      .join(', ');
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
      <XAxis
        dataKey="label"
        stroke="var(--muted-foreground)"
        fontSize={12}
        axisLine={false}
        tickLine={false}
      />
      <YAxis
        stroke="var(--muted-foreground)"
        fontSize={12}
        axisLine={false}
        tickLine={false}
        width={64}
        tickFormatter={(v: number) => formatValue(v, result.unit)}
      />
      <Tooltip
        contentStyle={TOOLTIP}
        formatter={(v: unknown) => formatValue(typeof v === 'number' ? v : null, result.unit)}
      />
      {negative && <ReferenceLine y={0} stroke="var(--border)" />}
    </>
  );

  return (
    <section
      aria-label={result.title}
      aria-busy={loading || undefined}
      className="flex w-full flex-col gap-[10px] rounded-lg border border-border bg-card px-da-xl py-da-lg"
    >
      <div className="flex items-center gap-da-md">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-foreground da-heading-3">{result.title}</h2>
          <p className="truncate text-muted-foreground da-caption">{result.subtitle_text}</p>
          {result.partial_text && <p className="text-warning da-caption">{result.partial_text}.</p>}
        </div>
        <Segmented<ChartKind>
          label="Kiểu biểu đồ"
          value={kind}
          onChange={onKind}
          items={[
            { value: 'bar', label: 'Cột' },
            { value: 'line', label: 'Đường' },
            { value: 'table', label: 'Bảng' },
          ]}
        />
      </div>

      {kind !== 'table' && n > 1 && (
        <ul className="flex gap-da-lg" aria-label="Chú giải">
          {result.series.map((s, i) => (
            <li key={s.key} className="flex items-center gap-da-sm text-foreground da-caption">
              <span className={cn('size-2.5 rounded-[2px]', seriesColor(i, n))} aria-hidden />
              {s.label}
            </li>
          ))}
        </ul>
      )}

      {kind !== 'table' && (
        <div
          role="img"
          aria-label={`${result.title}: ${result.rows.map((row) => `${row.label} (${describe(row)})`).join('; ')}`}
          className="w-full"
          style={{ height: PLOT }}
        >
          <ResponsiveContainer width="100%" height="100%">
            {kind === 'bar' ? (
              <BarChart data={data}>
                {axes}
                {result.series.map((s, i) => (
                  <Bar
                    key={s.key}
                    dataKey={s.key}
                    name={s.label}
                    fill={loading ? 'var(--subtle)' : seriesFill(i, n)}
                    radius={[3, 3, 0, 0]}
                    cursor={drill ? 'pointer' : undefined}
                    onClick={
                      drill ? (_: unknown, index: number) => drill(result.rows[index]) : undefined
                    }
                  />
                ))}
              </BarChart>
            ) : (
              <LineChart data={data}>
                {axes}
                {result.series.map((s, i) => (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={loading ? 'var(--border)' : seriesFill(i, n)}
                    strokeWidth={2}
                    dot={false}
                  />
                ))}
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      )}

      {kind !== 'table' && drill && (
        <ul
          aria-label="Đi sâu theo nhóm"
          className="sr-only focus-within:not-sr-only focus-within:flex focus-within:flex-wrap focus-within:gap-da-sm"
        >
          {result.rows.map((row) => (
            <li key={row.key}>
              <button
                type="button"
                onClick={() => drill(row)}
                aria-label={`Đi sâu: ${row.label} (${describe(row)})`}
                className="rounded-sm px-da-sm text-foreground da-caption hover:bg-subtle"
              >
                {row.label}
              </button>
            </li>
          ))}
        </ul>
      )}

      {kind === 'table' && <ValueTable result={result} bare />}

      <div className="flex flex-wrap gap-da-xl">{links}</div>
    </section>
  );
}

/* "Bảng giá trị" (2143:15894): the category, one column per series, and the change. */
export function ValueTable({ result, bare = false }: { result: ExploreResult; bare?: boolean }) {
  const hasDelta = result.series.length > 1;
  const view = useTableView(result.rows, {
    search: (row) =>
      `${row.label} ${row.values.map((value) => formatValue(value, result.unit)).join(' ')} ${hasDelta ? formatDelta(row.delta, result.unit) : ''}`,
    filters: [
      hasDelta
        ? {
            id: 'delta',
            label: 'Thay đổi',
            value: (row: ExploreRow) =>
              (row.delta ?? 0) > 0 ? 'Tăng' : (row.delta ?? 0) < 0 ? 'Giảm' : 'Không đổi',
          }
        : { id: 'category', label: result.category_label, value: (row: ExploreRow) => row.label },
    ],
    sorts: [
      { id: 'label', value: (row: ExploreRow) => row.label },
      ...result.series.map((series, index) => ({
        id: series.key,
        value: (row: ExploreRow) => row.values[index],
      })),
      ...(hasDelta ? [{ id: 'delta', value: (row: ExploreRow) => row.delta }] : []),
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm dòng…" searchLabel="Tìm trong bảng giá trị" />
      <table
        className={cn(
          'w-full border-separate border-spacing-0 overflow-hidden',
          !bare && 'rounded-lg border border-border bg-card',
        )}
      >
        <thead>
          <tr className="bg-subtle text-left text-muted-foreground da-label">
            <SortableTh view={view} column="label" className="px-da-lg py-da-sm font-medium">
              {result.category_label}
            </SortableTh>
            {result.series.map((s) => (
              <SortableTh
                key={s.key}
                view={view}
                column={s.key}
                className="w-[110px] px-da-lg py-da-sm font-medium"
              >
                {s.label}
              </SortableTh>
            ))}
            {hasDelta && (
              <SortableTh
                view={view}
                column="delta"
                className="w-[110px] px-da-lg py-da-sm font-medium"
              >
                Thay đổi
              </SortableTh>
            )}
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.key}>
              <td className="border-t border-border px-da-lg py-da-sm text-foreground da-body-small">
                {row.label}
              </td>
              {row.values.map((x, i) => (
                <td
                  key={result.series[i]?.key ?? i}
                  className="border-t border-border px-da-lg py-da-sm text-foreground da-mono"
                >
                  {formatValue(x, result.unit)}
                </td>
              ))}
              {hasDelta && (
                <td
                  className={cn(
                    'border-t border-border px-da-lg py-da-sm da-mono',
                    result.increase_is_bad && (row.delta ?? 0) > 0
                      ? 'text-error'
                      : 'text-foreground',
                  )}
                >
                  {formatDelta(row.delta, result.unit)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}
