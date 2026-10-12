/**
 * The data table under an answer. Users finish an analysis in a table — a
 * number without its rows cannot be checked. Sortable, paged in place
 * ("show more"), numeric columns right-aligned, values through formatVN.
 * Drawn as the bordered block of the v3.1 screens: card, border, radius-md, DA/Table rows.
 */
import { useEffect, useMemo, useState } from 'react';
import { Button } from '../../../design-system/Button';
import { Icon } from '../../../design-system/Icon';
import { Segmented } from '../../../design-system/Segmented';
import { cn } from '../../../lib/utils';
import { formatCell, formatNumber, isNumericColumn, toNumber } from '../../../lib/formatVN';
import { TRUNCATION_LABELS } from '../labels';
import type { Truncation } from '../types';

interface DataGridProps {
  columns: string[];
  columnUnits?: Record<string, string>;
  rows: Record<string, unknown>[];
  /** total rows the query returned (may exceed rows.length when the preview is capped) */
  rowCount?: number | null;
  capped?: boolean;
  truncation?: Truncation | null;
  pageSize?: number;
  title?: string;
  onExport?: () => void;
  fullResult?: {
    totalRows: number;
    pageSize?: number;
    loadRows: (params: { offset: number; limit: number; sort?: string; dir?: 'asc' | 'desc' }) => Promise<{
      columns: string[];
      rows: Record<string, unknown>[];
      total: number;
      offset: number;
      limit: number;
      has_more: boolean;
    }>;
    onExportXlsx?: () => Promise<void> | void;
  };
}

type Sort = { column: string; dir: 'asc' | 'desc' } | null;
type Mode = 'preview' | 'full';

function compare(a: unknown, b: unknown): number {
  const na = toNumber(a as never);
  const nb = toNumber(b as never);
  if (na !== null && nb !== null) return na - nb;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  return String(a).localeCompare(String(b), 'en');
}

export function DataGrid({ columns, columnUnits, rows, rowCount, capped, truncation, pageSize = 10, title = 'Bảng dữ liệu', onExport, fullResult }: DataGridProps) {
  const [sort, setSort] = useState<Sort>(null);
  const [visible, setVisible] = useState(pageSize);
  const [mode, setMode] = useState<Mode>('preview');
  const [fullOffset, setFullOffset] = useState(0);
  const [fullRows, setFullRows] = useState<Record<string, unknown>[]>([]);
  const [fullColumns, setFullColumns] = useState<string[]>(columns);
  const [fullTotal, setFullTotal] = useState(fullResult?.totalRows ?? rowCount ?? rows.length);
  const [fullHasMore, setFullHasMore] = useState(false);
  const [fullLoading, setFullLoading] = useState(false);
  const [fullError, setFullError] = useState<string | null>(null);
  const [xlsxLoading, setXlsxLoading] = useState(false);

  const fullPageSize = fullResult?.pageSize ?? 500;

  const numeric = useMemo(() => {
    const out: Record<string, boolean> = {};
    const sourceRows = mode === 'full' ? fullRows : rows;
    const sourceColumns = mode === 'full' ? fullColumns : columns;
    for (const c of sourceColumns) out[c] = isNumericColumn(sourceRows.map(r => r[c]));
    return out;
  }, [columns, fullColumns, fullRows, mode, rows]);

  useEffect(() => {
    if (!fullResult || mode !== 'full') return;
    let cancelled = false;
    setFullLoading(true);
    setFullError(null);
    fullResult.loadRows({
      offset: fullOffset,
      limit: fullPageSize,
      sort: sort?.column,
      dir: sort?.dir,
    }).then(page => {
      if (cancelled) return;
      setFullColumns(page.columns.length ? page.columns : columns);
      setFullRows(page.rows);
      setFullTotal(page.total);
      setFullHasMore(page.has_more);
    }).catch(e => {
      if (!cancelled) setFullError(e instanceof Error ? e.message : 'Không tải được toàn bộ kết quả');
    }).finally(() => {
      if (!cancelled) setFullLoading(false);
    });
    return () => { cancelled = true; };
  }, [columns, fullOffset, fullPageSize, fullResult, mode, sort]);

  const sorted = useMemo(() => {
    if (mode === 'full') return fullRows;
    if (!sort) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const v = compare(a[sort.column], b[sort.column]);
      return sort.dir === 'asc' ? v : -v;
    });
    return copy;
  }, [fullRows, mode, rows, sort]);

  const activeColumns = mode === 'full' ? fullColumns : columns;
  const shown = mode === 'full' ? sorted : sorted.slice(0, visible);
  const total = mode === 'full' ? fullTotal : rowCount ?? rows.length;
  const toggleSort = (column: string) => {
    setSort(prev => (prev?.column !== column ? { column, dir: 'asc' } : prev.dir === 'asc' ? { column, dir: 'desc' } : null));
    if (mode === 'full') setFullOffset(0);
  };
  const exportXlsx = async () => {
    if (!fullResult?.onExportXlsx || xlsxLoading) return;
    setXlsxLoading(true);
    try {
      await fullResult.onExportXlsx();
    } finally {
      setXlsxLoading(false);
    }
  };

  const truncNote = truncation?.reason
    ? truncation.reason === 'row_limit' && truncation.rows_returned
      ? `Kết quả bị cắt ở ${formatNumber(truncation.rows_returned)} dòng`
      : TRUNCATION_LABELS[truncation.reason] ?? `kết quả bị cắt (${truncation.reason})`
    : null;

  const linkClass = 'inline-flex items-center gap-da-xs text-primary hover:underline da-body-strong';

  return (
    <div data-testid="data-grid" className="flex flex-col overflow-hidden rounded-md border border-border bg-card">
      <div className="flex flex-wrap items-center gap-da-md border-b border-border px-da-lg py-da-sm">
        <Icon name="table" size={14} className="text-muted-foreground" />
        <span className="text-foreground da-body-strong">
          {title} · {formatNumber(total)} dòng
        </span>
        <span className="flex-1" />
        {fullResult?.loadRows && (
          <Segmented
            variant="outline"
            label="Phạm vi bảng"
            value={mode}
            onChange={m => { setMode(m); setFullOffset(0); }}
            items={[{ value: 'preview', label: 'Xem trước' }, { value: 'full', label: 'Toàn bộ' }]}
          />
        )}
        {onExport && (
          <Button kind="ghost" size="sm" icon="download" onClick={onExport} title="Tải bảng này dạng CSV (mở được bằng Excel)">
            CSV xem trước
          </Button>
        )}
        {fullResult?.onExportXlsx && (
          <Button kind="ghost" size="sm" icon="sheet" onClick={exportXlsx} loading={xlsxLoading} title="Tải Excel gồm trang Dữ liệu và Nguồn">
            {xlsxLoading ? 'Đang tạo…' : 'Excel kèm nguồn'}
          </Button>
        )}
      </div>
      {fullError && (
        <p role="alert" className="m-0 border-b border-border px-da-lg py-da-sm text-error da-caption">{fullError}</p>
      )}
      <div className={cn('overflow-x-auto', fullLoading && 'opacity-55')}>
        <table className="w-full border-collapse da-table">
          <thead>
            <tr>
              {activeColumns.map(c => {
                const active = sort?.column === c;
                return (
                  <th
                    key={c}
                    onClick={() => toggleSort(c)}
                    aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                    className={cn(
                      'cursor-pointer select-none whitespace-nowrap border-b border-border bg-background px-da-lg py-da-sm da-label',
                      numeric[c] ? 'text-right' : 'text-left',
                      active ? 'text-foreground' : 'text-muted-foreground',
                    )}
                  >
                    <span className={cn('inline-flex items-center gap-da-xs', numeric[c] && 'flex-row-reverse')}>
                      {c}{columnUnits?.[c] ? ` (${columnUnits[c]})` : ''}
                      <span aria-hidden className={active ? 'opacity-100' : 'opacity-40'}>
                        {active ? (sort!.dir === 'asc' ? '↑' : '↓') : '↕'}
                      </span>
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, activeColumns.length)} className="px-da-lg py-da-xl text-center text-muted-foreground">
                  Không có dòng nào khớp điều kiện
                </td>
              </tr>
            ) : shown.map((row, i) => (
              <tr key={i} className="border-b border-border last:border-b-0">
                {activeColumns.map(c => (
                  <td
                    key={c}
                    className={cn(
                      'whitespace-nowrap px-da-lg py-da-sm text-foreground tabular-nums',
                      numeric[c] ? 'text-right da-mono' : 'text-left',
                    )}
                  >
                    {formatCell(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-da-lg border-t border-border px-da-lg py-da-sm text-muted-foreground da-caption">
        <span>
          {mode === 'full'
            ? `Toàn bộ ${formatNumber(shown.length ? fullOffset + 1 : 0)}-${formatNumber(fullOffset + shown.length)} / ${formatNumber(fullTotal)} dòng`
            : `Xem trước ${formatNumber(shown.length)} / ${formatNumber(rows.length)} dòng${capped ? ' (bản xem trước)' : ''}`}
        </span>
        {fullLoading && <span>Đang tải toàn bộ kết quả…</span>}
        {truncNote && <span className="text-warning">{truncNote}</span>}
        {mode === 'preview' && visible < sorted.length && (
          <button type="button" className={linkClass} onClick={() => setVisible(v => v + pageSize * 2)}>
            Xem thêm {formatNumber(Math.min(pageSize * 2, sorted.length - visible))} dòng
          </button>
        )}
        {mode === 'preview' && visible > pageSize && sorted.length > pageSize && (
          <button type="button" className={linkClass} onClick={() => setVisible(pageSize)}>Thu gọn</button>
        )}
        {mode === 'full' && fullOffset > 0 && (
          <button type="button" className={linkClass} onClick={() => setFullOffset(Math.max(0, fullOffset - fullPageSize))}>
            ← Trang trước
          </button>
        )}
        {mode === 'full' && fullHasMore && (
          <button type="button" className={linkClass} onClick={() => setFullOffset(fullOffset + fullPageSize)}>
            Trang sau →
          </button>
        )}
      </div>
    </div>
  );
}
