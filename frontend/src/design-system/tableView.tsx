import { useMemo, useState, type ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Input } from './Input';
import { Pagination } from './Pagination';
import { Select } from './Select';

/**
 * Client-side search, filter, sort and pagination for a record table.
 * Pages keep their own cells; this owns the four controls every list must have.
 */
export const TABLE_PAGE_SIZE = 20;
const ALL = '_all';

export type SortDir = 'asc' | 'desc';

export type TableFilter<T> = {
  id: string;
  /** Shown in the option, as "Chế độ: truy vấn tại chỗ". */
  label: string;
  /** Accessible name of the control. Defaults to "Lọc theo {label}". */
  control?: string;
  value: (row: T) => string;
  text?: (value: string) => string;
};

export type TableSort<T> = {
  id: string;
  value: (row: T) => string | number | null | undefined;
};

export type TableView<T> = {
  rows: T[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  query: string;
  setQuery: (query: string) => void;
  filters: TableFilter<T>[];
  filter: Record<string, string>;
  setFilter: (id: string, value: string) => void;
  options: Record<string, { value: string; label: string }[]>;
  sort: { id: string; dir: SortDir } | null;
  toggleSort: (id: string) => void;
};

function compareValues(
  a: string | number | null | undefined,
  b: string | number | null | undefined,
): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'vi', { numeric: true, sensitivity: 'base' });
}

export function useTableView<T>(
  source: readonly T[],
  config: {
    search: (row: T) => string;
    filters?: TableFilter<T>[];
    /** Extra predicate from a control the page draws itself (a status chip row). */
    predicate?: (row: T) => boolean;
    sorts: TableSort<T>[];
    pageSize?: number;
  },
): TableView<T> {
  const filters = config.filters ?? [];
  const pageSize = config.pageSize ?? TABLE_PAGE_SIZE;
  const [query, setQueryState] = useState('');
  const [filter, setFilterState] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ id: string; dir: SortDir } | null>(null);
  const [page, setPage] = useState(1);

  const options = useMemo(() => {
    const out: Record<string, { value: string; label: string }[]> = {};
    for (const spec of filters) {
      const values = [...new Set(source.map((row) => spec.value(row)).filter(Boolean))].sort((a, b) =>
        a.localeCompare(b, 'vi'),
      );
      out[spec.id] = [
        { value: ALL, label: `${spec.label}: tất cả` },
        ...values.map((value) => ({
          value,
          label: `${spec.label}: ${spec.text ? spec.text(value) : value}`,
        })),
      ];
    }
    return out;
  }, [filters, source]);

  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = source.filter((row) => {
      if (q && !config.search(row).toLowerCase().includes(q)) return false;
      if (config.predicate && !config.predicate(row)) return false;
      return filters.every((spec) => {
        const chosen = filter[spec.id] ?? ALL;
        return chosen === ALL || spec.value(row) === chosen;
      });
    });
    if (!sort) return rows;
    const spec = config.sorts.find((item) => item.id === sort.id);
    if (!spec) return rows;
    return [...rows].sort((a, b) => {
      const order = compareValues(spec.value(a), spec.value(b));
      return sort.dir === 'asc' ? order : -order;
    });
  }, [config, filter, filters, query, sort, source]);

  const pageCount = Math.max(1, Math.ceil(matched.length / pageSize));
  const current = Math.min(page, pageCount);
  const start = (current - 1) * pageSize;

  const setQuery = (next: string) => {
    setQueryState(next);
    setPage(1);
  };
  const setFilter = (id: string, value: string) => {
    setFilterState((prev) => ({ ...prev, [id]: value }));
    setPage(1);
  };
  const toggleSort = (id: string) => {
    setSort((prev) =>
      prev?.id !== id ? { id, dir: 'asc' } : prev.dir === 'asc' ? { id, dir: 'desc' } : null,
    );
    setPage(1);
  };

  return {
    rows: matched.slice(start, start + pageSize),
    total: matched.length,
    page: current,
    pageSize,
    setPage,
    query,
    setQuery,
    filters,
    filter,
    setFilter,
    options,
    sort,
    toggleSort,
  };
}

export function TableToolbar<T>({
  view,
  placeholder = 'Tìm…',
  searchLabel = 'Tìm',
  children,
}: {
  view: TableView<T>;
  placeholder?: string;
  searchLabel?: string;
  /** A page-specific filter (status chips) drawn beside the search box. */
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-da-md">
      <Input
        className="w-[300px] max-[639px]:w-full"
        icon="search"
        placeholder={placeholder}
        aria-label={searchLabel}
        value={view.query}
        onChange={(event) => view.setQuery(event.target.value)}
      />
      {view.filters.map((spec) => (
        <Select
          key={spec.id}
          className="w-[220px] max-[639px]:w-full"
          size="sm"
          label={spec.control ?? `Lọc theo ${spec.label}`}
          value={view.filter[spec.id] ?? ALL}
          onChange={(value) => view.setFilter(spec.id, value)}
          options={view.options[spec.id] ?? [{ value: ALL, label: `${spec.label}: tất cả` }]}
        />
      ))}
      {children}
    </div>
  );
}

export function SortableTh<T>({
  view,
  column,
  className,
  children,
}: {
  view: TableView<T>;
  column?: string;
  className?: string;
  children?: ReactNode;
}) {
  const active = column != null && view.sort?.id === column;
  const dir = view.sort?.dir;
  return (
    <th
      className={cn(column && 'cursor-pointer select-none', className)}
      aria-sort={column ? (active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none') : undefined}
      onClick={column ? () => view.toggleSort(column) : undefined}
    >
      <span className="inline-flex items-center gap-da-xs">
        {children}
        {column && (
          <span aria-hidden className={active ? 'opacity-100' : 'opacity-40'}>
            {active ? (view.sort!.dir === 'asc' ? '↑' : '↓') : '↕'}
          </span>
        )}
      </span>
    </th>
  );
}

export function TablePager<T>({ view }: { view: TableView<T> }) {
  return <Pagination page={view.page} pageSize={view.pageSize} total={view.total} onPage={view.setPage} />;
}

/** A grid whose columns come from the data (a query result, a sample, a preview). */
export function MatrixTable({
  columns,
  rows,
  className,
  highlightRow,
  searchLabel = 'Tìm trong bảng',
}: {
  columns: readonly string[];
  rows: readonly (readonly (string | number | null | undefined)[])[];
  className?: string;
  highlightRow?: number | null;
  searchLabel?: string;
}) {
  const items = rows.map((cells, index) => ({ index, cells }));
  const view = useTableView(items, {
    search: (row) => row.cells.map((cell) => String(cell ?? '')).join(' '),
    filters: columns[0]
      ? [{ id: 'first', label: columns[0], value: (row) => String(row.cells[0] ?? '') }]
      : [],
    sorts: columns.map((name, index) => ({
      id: name || String(index),
      value: (row) => row.cells[index] ?? '',
    })),
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm trong bảng…" searchLabel={searchLabel} />
      <div className="overflow-x-auto">
        <table className={className}>
          <thead>
            <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
              {columns.map((column, index) => (
                <SortableTh
                  key={column || index}
                  view={view}
                  column={column || String(index)}
                  className="px-da-lg py-da-md font-normal"
                >
                  {column}
                </SortableTh>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row) => (
              <tr
                key={row.index}
                className={cn(
                  'border-b border-border text-foreground last:border-b-0',
                  highlightRow === row.index && 'bg-accent',
                )}
              >
                {columns.map((_, index) => (
                  <td key={index} className="px-da-lg py-[10px]">
                    {row.cells[index] ?? '—'}
                  </td>
                ))}
              </tr>
            ))}
            {view.total === 0 && tableEmptyRow(Math.max(columns.length, 1))}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}

export function tableEmptyRow(colSpan: number, message = 'Không có dòng nào khớp.') {
  return (
    <tr>
      <td colSpan={colSpan} className="px-da-lg py-da-xl text-muted-foreground da-body-small">
        {message}
      </td>
    </tr>
  );
}
