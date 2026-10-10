import { Segmented } from '../../design-system/Segmented';
import type { ExploreResult, ExploreRow } from '../../lib/v1/explore';
import { cn } from '../../lib/utils';
import { formatDelta, formatValue } from './format';

/*
 * The chart card of "v3.1 · Khám phá" (2140:15648): title, the metric version and timing, the
 * Cột / Đường / Bảng switch (it changes the drawing, never the data), the legend, a 200px plot
 * with the category labels under it, and the links under the chart. The first series is
 * chart-6 and the last chart-1, as the frame compares an earlier period with the latest.
 * While a new query runs, the bars of the last result turn subtle ("Khám phá · Đang tải").
 * A click on a column group drills into it when the result names a drill dimension.
 */
export type ChartKind = 'bar' | 'line' | 'table';

const PLOT = 200;

function seriesColor(index: number, count: number): string {
  if (index === count - 1) return 'bg-chart-1';
  return ['bg-chart-6', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5'][index % 5];
}

const STROKE: Record<string, string> = {
  'bg-chart-1': 'stroke-chart-1',
  'bg-chart-2': 'stroke-chart-2',
  'bg-chart-3': 'stroke-chart-3',
  'bg-chart-4': 'stroke-chart-4',
  'bg-chart-5': 'stroke-chart-5',
  'bg-chart-6': 'stroke-chart-6',
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
  const max = Math.max(0, ...result.rows.flatMap((r) => r.values.map((x) => x ?? 0))) * 1.08 || 1;
  const n = result.series.length;

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

      {kind === 'bar' && (
        <div className="flex w-full flex-col">
          <div
            className="flex w-full items-end justify-around border-b border-border"
            style={{ height: PLOT }}
          >
            {result.rows.map((row) => {
              const values = row.values
                .map((x, i) => `${result.series[i]?.label}: ${formatValue(x, result.unit)}`)
                .join(', ');
              const bars = row.values.map((x, i) => (
                <span
                  key={result.series[i]?.key ?? i}
                  className={cn(
                    'w-7 rounded-[3px] transition-[height] duration-200',
                    loading ? 'bg-subtle' : seriesColor(i, n),
                  )}
                  style={{ height: Math.max(2, ((x ?? 0) / max) * PLOT) }}
                />
              ));
              return onDrill && !loading ? (
                <button
                  key={row.key}
                  type="button"
                  onClick={() => onDrill(row)}
                  aria-label={`Đi sâu: ${row.label} (${values})`}
                  className="flex h-full items-end gap-da-xs rounded-sm hover:bg-subtle"
                >
                  {bars}
                </button>
              ) : (
                <span
                  key={row.key}
                  role="img"
                  aria-label={`${row.label}: ${values}`}
                  className="flex h-full items-end gap-da-xs"
                >
                  {bars}
                </span>
              );
            })}
          </div>
          <CategoryLabels rows={result.rows} />
        </div>
      )}

      {kind === 'line' && (
        <div className="flex w-full flex-col">
          <svg
            viewBox={`0 0 100 ${PLOT}`}
            preserveAspectRatio="none"
            className="w-full border-b border-border"
            style={{ height: PLOT }}
            role="img"
            aria-label={result.title}
          >
            {result.series.map((s, si) => {
              const step = 100 / result.rows.length;
              const points = result.rows
                .map(
                  (r, ri) => `${step * ri + step / 2},${PLOT - ((r.values[si] ?? 0) / max) * PLOT}`,
                )
                .join(' ');
              return (
                <polyline
                  key={s.key}
                  points={points}
                  fill="none"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                  className={loading ? 'stroke-border' : STROKE[seriesColor(si, n)]}
                />
              );
            })}
          </svg>
          <CategoryLabels rows={result.rows} />
        </div>
      )}

      {kind === 'table' && <ValueTable result={result} bare />}

      <div className="flex flex-wrap gap-da-xl">{links}</div>
    </section>
  );
}

function CategoryLabels({ rows }: { rows: ExploreRow[] }) {
  return (
    <div className="flex w-full justify-around pt-da-md" aria-hidden>
      {rows.map((row) => (
        <span key={row.key} className="truncate px-da-xs text-muted-foreground da-caption">
          {row.short_label}
        </span>
      ))}
    </div>
  );
}

/* "Bảng giá trị" (2143:15894): the category, one column per series, and the change. */
export function ValueTable({ result, bare = false }: { result: ExploreResult; bare?: boolean }) {
  const hasDelta = result.series.length > 1;
  return (
    <table
      className={cn(
        'w-full border-separate border-spacing-0 overflow-hidden',
        !bare && 'rounded-lg border border-border bg-card',
      )}
    >
      <thead>
        <tr className="bg-subtle text-left text-muted-foreground da-label">
          <th className="px-da-lg py-da-sm font-medium">{result.category_label}</th>
          {result.series.map((s) => (
            <th key={s.key} className="w-[110px] px-da-lg py-da-sm font-medium">
              {s.label}
            </th>
          ))}
          {hasDelta && <th className="w-[110px] px-da-lg py-da-sm font-medium">Thay đổi</th>}
        </tr>
      </thead>
      <tbody>
        {result.rows.map((row) => (
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
                  result.increase_is_bad && (row.delta ?? 0) > 0 ? 'text-error' : 'text-foreground',
                )}
              >
                {formatDelta(row.delta, result.unit)}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
