import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';
import type { ForecastStat } from '../../lib/v1/forecast';

/* Pieces the four P15 screens share: the stat tile and the bordered table of the Figma frames. */

const STAT_TONE: Record<ForecastStat['tone'], string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  neutral: 'text-muted-foreground',
};

/** "Stat · Chuỗi mục tiêu": caption, the value in DA/Metric, a toned note. */
export function Stat({ stat }: { stat: ForecastStat }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-da-sm rounded-lg border border-border bg-card px-da-xl py-[14px]">
      <p className="text-muted-foreground da-caption">{stat.label}</p>
      <p className="text-foreground da-metric">{stat.value}</p>
      <p className={cn('da-caption', STAT_TONE[stat.tone])}>{stat.note}</p>
    </div>
  );
}

export interface Column<T> {
  head: string;
  /** Tailwind width class; the last column usually takes the rest. */
  width?: string;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Figma leaves some heads blank; the name stays for screen readers. */
  hideHead?: boolean;
}

/** The Figma "Table": radius 8, a subtle head row, 10px rows divided by the border. */
export function DataTable<T>({
  label,
  columns,
  rows,
  rowKey,
  rowClassName,
}: {
  label: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowClassName?: (row: T) => string | undefined;
}) {
  return (
    <div className="w-full overflow-x-auto rounded-md border border-border bg-card">
      <table aria-label={label} className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-subtle text-muted-foreground da-caption">
            {columns.map((c) => (
              <th key={c.head} scope="col" className={cn('px-da-lg py-da-md font-normal', c.width)}>
                {c.hideHead ? <span className="sr-only">{c.head}</span> : c.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={cn('border-b border-border last:border-b-0', rowClassName?.(row))}
            >
              {columns.map((c) => (
                <td
                  key={c.head}
                  className={cn('px-da-lg py-[10px] text-foreground da-body-small', c.className)}
                >
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
