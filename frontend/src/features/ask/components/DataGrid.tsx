/**
 * The data table under an answer. Users finish an analysis in a table — a
 * number without its rows cannot be checked. Sortable, paged in place
 * ("show more"), numeric columns right-aligned, values through formatVN.
 */
import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowUpDown, Download, FileSpreadsheet, Table2 } from 'lucide-react';
import { formatCell, formatNumber, isNumericColumn, toNumber } from '../../../lib/formatVN';
import { TRUNCATION_LABELS } from '../labels';
import type { Truncation } from '../types';

interface DataGridProps {
  columns: string[];
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

export function DataGrid({ columns, rows, rowCount, capped, truncation, pageSize = 10, title = 'Data table', onExport, fullResult }: DataGridProps) {
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
      if (!cancelled) setFullError(e instanceof Error ? e.message : 'Could not load the full result');
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

  if (!columns.length || !rows.length) return null;

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
    ? TRUNCATION_LABELS[truncation.reason] ?? `result truncated (${truncation.reason})`
    : null;

  return (
    <div data-testid="data-grid" style={{ marginTop: 10, border: '1px solid var(--ab-border)', borderRadius: 10, overflow: 'hidden', background: 'var(--ab-bg)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: '1px solid var(--ab-border)' }}>
        <Table2 size={12} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ab-muted)' }}>
          {title} · {formatNumber(total)} rows
        </span>
        {fullResult?.loadRows && (
          <div style={{ display: 'flex', gap: 3, padding: 2, border: '1px solid var(--ab-border)', borderRadius: 8, marginLeft: 'auto' }}>
            {(['preview', 'full'] as Mode[]).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => { setMode(m); setFullOffset(0); }}
                className="ab-btn"
                style={{ border: 0, background: mode === m ? 'var(--ab-accent)' : 'transparent', color: mode === m ? '#fff' : 'var(--ab-fg-2)' }}
              >
                {m === 'preview' ? 'Preview' : 'Full result'}
              </button>
            ))}
          </div>
        )}
        {onExport && (
          <button
            type="button"
            onClick={onExport}
            className="ab-btn"
            style={{ marginLeft: fullResult?.loadRows ? 0 : 'auto' }}
            title="Download this table as CSV (opens in Excel)"
          >
            <Download size={11} /> CSV preview
          </button>
        )}
        {fullResult?.onExportXlsx && (
          <button
            type="button"
            onClick={exportXlsx}
            className="ab-btn"
            disabled={xlsxLoading}
            title="Download Excel with Data and Source sheets"
          >
            <FileSpreadsheet size={11} /> {xlsxLoading ? 'Generating…' : 'Excel + Source'}
          </button>
        )}
      </div>
      {fullError && (
        <div style={{ padding: '6px 10px', color: 'var(--ab-bad)', fontSize: 11, borderBottom: '1px solid var(--ab-border)' }}>
          {fullError}
        </div>
      )}
      <div style={{ overflowX: 'auto', opacity: fullLoading ? 0.55 : 1 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {activeColumns.map(c => {
                const active = sort?.column === c;
                const Icon = active ? (sort!.dir === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown;
                return (
                  <th
                    key={c}
                    onClick={() => toggleSort(c)}
                    aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                    style={{
                      padding: '6px 10px', textAlign: numeric[c] ? 'right' : 'left', whiteSpace: 'nowrap',
                      fontSize: 10.5, fontWeight: 700, letterSpacing: '0.03em', color: active ? 'var(--ab-accent)' : 'var(--ab-muted)',
                      background: 'var(--ab-bg-2)', borderBottom: '1px solid var(--ab-border)', cursor: 'pointer', userSelect: 'none',
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, flexDirection: numeric[c] ? 'row-reverse' : 'row' }}>
                      {c}
                      <Icon size={10} style={{ opacity: active ? 1 : 0.4 }} />
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--ab-border)' }}>
                {activeColumns.map(c => (
                  <td
                    key={c}
                    style={{
                      padding: '5px 10px', whiteSpace: 'nowrap', color: 'var(--ab-fg-2)',
                      textAlign: numeric[c] ? 'right' : 'left',
                      fontFamily: numeric[c] ? 'var(--font-mono)' : undefined,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {formatCell(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px', fontSize: 10.5, color: 'var(--ab-muted)', flexWrap: 'wrap' }}>
        <span>
          {mode === 'full'
            ? `Full result ${formatNumber(fullOffset + 1)}-${formatNumber(fullOffset + shown.length)} / ${formatNumber(fullTotal)} rows`
            : `Preview ${formatNumber(shown.length)} / ${formatNumber(rows.length)} rows${capped ? ' (preview)' : ''}`}
        </span>
        {fullLoading && <span>Loading full result…</span>}
        {truncNote && <span style={{ color: 'var(--ab-warn)' }}>{truncNote}</span>}
        {mode === 'preview' && visible < sorted.length && (
          <button type="button" className="ab-link" onClick={() => setVisible(v => v + pageSize * 2)}>
            Show {formatNumber(Math.min(pageSize * 2, sorted.length - visible))} more rows
          </button>
        )}
        {mode === 'preview' && visible > pageSize && sorted.length > pageSize && (
          <button type="button" className="ab-link" onClick={() => setVisible(pageSize)}>Collapse</button>
        )}
        {mode === 'full' && fullOffset > 0 && (
          <button type="button" className="ab-link" onClick={() => setFullOffset(Math.max(0, fullOffset - fullPageSize))}>
            <ArrowLeft size={10} /> Previous page
          </button>
        )}
        {mode === 'full' && fullHasMore && (
          <button type="button" className="ab-link" onClick={() => setFullOffset(fullOffset + fullPageSize)}>
            Next page <ArrowRight size={10} />
          </button>
        )}
      </div>
    </div>
  );
}
