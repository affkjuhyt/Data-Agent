import type { ReactNode } from 'react';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { cn } from '../../lib/utils';
import type { ForecastStat } from '../../lib/v1/forecast';

/* Pieces the four P15 screens share: the stat tile and the bordered table of the Figma frames. */

const STAT_TONE: Record<ForecastStat['tone'], string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  neutral: 'text-muted-foreground',
  default: 'text-foreground',
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
  /** Plain value for search, sort and the Select filter when the cell is markup. */
  value?: (row: T) => string | number | null | undefined;
  /** This column is the Select filter. Otherwise the column with the fewest distinct values is. */
  filter?: boolean;
}

function columnValue<T>(column: Column<T>, row: T): string | number | null | undefined {
  if (column.value) return column.value(row);
  const rendered = column.cell(row);
  if (typeof rendered === 'string' || typeof rendered === 'number') return rendered;
  return undefined;
}

function filterColumn<T>(columns: Column<T>[], rows: readonly T[]): Column<T> | undefined {
  const marked = columns.find((column) => column.filter);
  if (marked) return marked;
  let best: { column: Column<T>; count: number } | undefined;
  for (const column of columns) {
    const values = new Set<string>();
    for (const row of rows) {
      const value = columnValue(column, row);
      if (value != null && value !== '') values.add(String(value));
    }
    if (values.size < 2) continue;
    if (!best || values.size < best.count) best = { column, count: values.size };
  }
  return best?.column ?? columns[0];
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
  const chosen = filterColumn(columns, rows);
  const view = useTableView(rows, {
    search: (row) => columns.map((column) => columnValue(column, row) ?? '').join(' '),
    filters: chosen
      ? [
          {
            id: chosen.head,
            label: chosen.head,
            value: (row) => {
              const value = columnValue(chosen, row);
              return value == null ? '' : String(value);
            },
          },
        ]
      : [],
    sorts: columns.map((column) => ({
      id: column.head,
      value: (row: T) => columnValue(column, row),
    })),
    pageSize: 50,
  });
  return (
    <div className="flex w-full flex-col gap-da-md">
      <TableToolbar view={view} placeholder={`Tìm trong ${label}`} searchLabel={`Tìm ${label}`} />
      <div className="w-full overflow-x-auto rounded-md border border-border bg-card">
      <table aria-label={label} className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-subtle text-muted-foreground da-caption">
            {columns.map((c) => (
              <SortableTh
                key={c.head}
                view={view}
                column={c.head}
                className={cn('px-da-lg py-da-md font-normal', c.width)}
              >
                {c.hideHead ? <span className="sr-only">{c.head}</span> : c.head}
              </SortableTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
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
      <TablePager view={view} />
    </div>
  );
}

/** Saves a CSV; a BOM so Excel opens the Vietnamese text as UTF-8. */
export function downloadCsv(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(['\ufeff', text], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
