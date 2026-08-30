/**
 * The data table under an answer. Vietnamese users finish an analysis in a
 * table — a number without its rows cannot be checked. Sortable, paged in
 * place ("xem thêm"), numeric columns right-aligned, values through formatVN.
 */
import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Download, Table2 } from 'lucide-react';
import { formatCell, formatNumber, isNumericColumn, toNumber } from '../../../lib/formatVN';
import { TRUNCATION_LABELS } from '../labels';
import type { Truncation } from '../types';

export interface DataGridProps {
  columns: string[];
  rows: Record<string, unknown>[];
  /** total rows the query returned (may exceed rows.length when the preview is capped) */
  rowCount?: number | null;
  capped?: boolean;
  truncation?: Truncation | null;
  pageSize?: number;
  title?: string;
  onExport?: () => void;
}

type Sort = { column: string; dir: 'asc' | 'desc' } | null;

function compare(a: unknown, b: unknown): number {
  const na = toNumber(a as never);
  const nb = toNumber(b as never);
  if (na !== null && nb !== null) return na - nb;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  return String(a).localeCompare(String(b), 'vi');
}

export function DataGrid({ columns, rows, rowCount, capped, truncation, pageSize = 10, title = 'Bảng dữ liệu', onExport }: DataGridProps) {
  const [sort, setSort] = useState<Sort>(null);
  const [visible, setVisible] = useState(pageSize);

  const numeric = useMemo(() => {
    const out: Record<string, boolean> = {};
    for (const c of columns) out[c] = isNumericColumn(rows.map(r => r[c]));
    return out;
  }, [columns, rows]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const v = compare(a[sort.column], b[sort.column]);
      return sort.dir === 'asc' ? v : -v;
    });
    return copy;
  }, [rows, sort]);

  if (!columns.length || !rows.length) return null;

  const shown = sorted.slice(0, visible);
  const total = rowCount ?? rows.length;
  const toggleSort = (column: string) =>
    setSort(prev => (prev?.column !== column ? { column, dir: 'asc' } : prev.dir === 'asc' ? { column, dir: 'desc' } : null));

  const truncNote = truncation?.reason
    ? TRUNCATION_LABELS[truncation.reason] ?? `kết quả bị cắt (${truncation.reason})`
    : null;

  return (
    <div data-testid="data-grid" style={{ marginTop: 10, border: '1px solid var(--ab-border)', borderRadius: 10, overflow: 'hidden', background: 'var(--ab-bg)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: '1px solid var(--ab-border)' }}>
        <Table2 size={12} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ab-muted)' }}>
          {title} · {formatNumber(total)} dòng
        </span>
        {onExport && (
          <button
            type="button"
            onClick={onExport}
            className="ab-btn"
            style={{ marginLeft: 'auto' }}
            title="Tải bảng này dưới dạng CSV (mở được bằng Excel)"
          >
            <Download size={11} /> Xuất CSV
          </button>
        )}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {columns.map(c => {
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
                {columns.map(c => (
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
        <span>Hiển thị {formatNumber(shown.length)} / {formatNumber(rows.length)} dòng{capped ? ' (xem trước)' : ''}</span>
        {truncNote && <span style={{ color: 'var(--ab-warn)' }}>⚠ {truncNote}</span>}
        {visible < sorted.length && (
          <button type="button" className="ab-link" onClick={() => setVisible(v => v + pageSize * 2)}>
            Xem thêm {formatNumber(Math.min(pageSize * 2, sorted.length - visible))} dòng
          </button>
        )}
        {visible > pageSize && sorted.length > pageSize && (
          <button type="button" className="ab-link" onClick={() => setVisible(pageSize)}>Thu gọn</button>
        )}
      </div>
    </div>
  );
}
