import React, { useMemo, useRef, useState } from 'react';
import { cn } from '../../lib/utils';
import { Search, Filter, ChevronRight, Download } from 'lucide-react';
import { EmptyState } from '../ui';

interface Column<T> {
  header: string;
  accessor: keyof T | ((item: T) => React.ReactNode);
  className?: string;
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  title?: string;
  subtitle?: string;
  onRowClick?: (item: T) => void;
  actions?: React.ReactNode;
  showSearch?: boolean;
}

/** Plain-text projection of a rendered cell — used for search + CSV export. */
function nodeText(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join(' ');
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) return nodeText(node.props.children);
  return '';
}

function renderCell<T>(item: T, col: Column<T>): React.ReactNode {
  return typeof col.accessor === 'function' ? col.accessor(item) : (item[col.accessor] as React.ReactNode);
}

function csvEscape(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function DataTable<T extends { id: string | number }>({
  data,
  columns,
  title,
  subtitle,
  onRowClick,
  actions,
  showSearch = true
}: DataTableProps<T>) {
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return data;
    return data.filter(item => columns.some(col => nodeText(renderCell(item, col)).toLowerCase().includes(q)));
  }, [data, columns, query]);

  const exportCsv = () => {
    const header = columns.map(c => csvEscape(c.header)).join(',');
    const body = rows.map(item => columns.map(c => csvEscape(nodeText(renderCell(item, c)))).join(','));
    const blob = new Blob(['﻿' + [header, ...body].join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(title || 'table').replace(/[^\w.-]+/g, '_').toLowerCase()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const empty = (
    <EmptyState
      icon={Search}
      title={query ? 'Không có kết quả khớp' : 'Không có dữ liệu'}
      description={query ? `Không có dòng nào khớp với "${query}".` : undefined}
      action={query ? <button type="button" className="btn-ghost btn-sm" onClick={() => setQuery('')}>Xoá bộ lọc</button> : undefined}
    />
  );

  return (
    <div className="space-y-5">
      {(title || showSearch || actions) && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            {title && <h3 className="text-xl font-bold text-[#111111] tracking-tight font-display">{title}</h3>}
            {subtitle && <p className="text-[11px] text-[#5F5F5F] uppercase font-bold tracking-widest mt-1">{subtitle}</p>}
          </div>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
            {showSearch && (
              <div className="relative group flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#5F5F5F] group-focus-within:text-[#8A5A00] transition-colors" />
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Lọc dữ liệu…"
                  aria-label="Lọc dữ liệu"
                  className="ui-input w-full py-2.5 pl-10 pr-4 text-xs h-10"
                />
              </div>
            )}
            <div className="flex items-center gap-2">
              {actions}
              <div className="flex items-center gap-1 shrink-0">
                {showSearch && (
                  <button
                    type="button"
                    onClick={() => searchRef.current?.focus()}
                    title="Lọc dữ liệu"
                    aria-label="Lọc dữ liệu"
                    className="btn-ghost btn-sm"
                  >
                    <Filter className="w-4 h-4" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={exportCsv}
                  disabled={rows.length === 0}
                  title="Tải CSV"
                  aria-label="Tải CSV"
                  className="btn-ghost btn-sm"
                >
                  <Download className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {rows.length === 0 ? empty : (
        <>
          {/* Desktop table */}
          <div className="content-card content-card--static hidden lg:block overflow-hidden">
            <div className="overflow-x-auto">
              <table className="ui-table">
                <thead>
                  <tr>
                    {columns.map((col, i) => (
                      <th key={i} className={col.className}>{col.header}</th>
                    ))}
                    {onRowClick && <th className="w-10" aria-hidden="true" />}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr
                      key={item.id}
                      onClick={onRowClick ? () => onRowClick(item) : undefined}
                      className={cn(onRowClick && 'cursor-pointer')}
                    >
                      {columns.map((col, i) => (
                        <td key={i} className={col.className}>{renderCell(item, col)}</td>
                      ))}
                      {onRowClick && (
                        <td className="text-right">
                          <ChevronRight className="w-4 h-4 text-[#5F5F5F] inline-block" aria-hidden="true" />
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Mobile cards */}
          <div className="lg:hidden space-y-3">
            {rows.map((item) => (
              <div
                key={item.id}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
                role={onRowClick ? 'button' : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={onRowClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(item); } } : undefined}
                className={cn('content-card p-4 space-y-3', onRowClick ? 'cursor-pointer' : 'content-card--static')}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="font-bold text-[#111111] text-base min-w-0">{renderCell(item, columns[0])}</div>
                  {onRowClick && <ChevronRight className="w-4 h-4 text-[#5F5F5F] shrink-0" aria-hidden="true" />}
                </div>
                <div className="grid grid-cols-1 gap-3">
                  {columns.slice(1).map((col, i) => (
                    <div key={i} className="space-y-0.5">
                      <div className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-widest">{col.header}</div>
                      <div className="text-sm text-[#111111]">{renderCell(item, col)}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
