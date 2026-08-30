import React from 'react';
import { cn } from '../../lib/utils';
import { Search, Filter, MoreVertical, ChevronDown, Download, Settings2 } from 'lucide-react';

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

export function DataTable<T extends { id: string | number }>({ 
  data, 
  columns, 
  title, 
  subtitle, 
  onRowClick,
  actions,
  showSearch = true
}: DataTableProps<T>) {
  return (
    <div className="space-y-6">
      {(title || showSearch || actions) && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            {title && <h3 className="text-xl font-bold text-white tracking-tight">{title}</h3>}
            {subtitle && <p className="text-xs text-slate-500 uppercase font-black tracking-widest mt-1 italic">{subtitle}</p>}
          </div>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
            {showSearch && (
              <div className="relative group flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 group-focus-within:text-brand-400 transition-colors" />
                <input 
                  type="text" 
                  placeholder="Filter resources..." 
                  className="w-full bg-white/5 border border-white/10 rounded-xl py-2.5 pl-10 pr-4 text-xs focus:outline-none focus:ring-1 focus:ring-brand-500/50 transition-all font-medium h-10"
                />
              </div>
            )}
            <div className="flex items-center gap-2">
              {actions}
              <div className="flex bg-white/5 border border-white/5 rounded-xl p-1 shrink-0">
                <button className="p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all"><Filter className="w-4 h-4" /></button>
                <button className="hidden sm:block p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all"><Settings2 className="w-4 h-4" /></button>
                <button className="p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all"><Download className="w-4 h-4" /></button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Desktop Table View */}
      <div className="hidden lg:block overflow-hidden rounded-[32px] border border-white/5 bg-white/[0.01] shadow-2xl shadow-black/40">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-white/5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] bg-white/[0.02]">
              {columns.map((col, i) => (
                <th key={i} className={cn("px-8 py-5", col.className)}>
                  <div className="flex items-center gap-2">
                    {col.header}
                    <ChevronDown className="w-3 h-3 opacity-30" />
                  </div>
                </th>
              ))}
              <th className="px-8 py-5 text-right w-16"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {data.map((item) => (
              <tr 
                key={item.id} 
                onClick={() => onRowClick?.(item)}
                className={cn(
                  "group transition-all",
                  onRowClick ? "cursor-pointer hover:bg-white/[0.04]" : "hover:bg-white/[0.02]"
                )}
              >
                {columns.map((col, i) => (
                  <td key={i} className={cn("px-8 py-6", col.className)}>
                    {typeof col.accessor === 'function' 
                      ? col.accessor(item) 
                      : (item[col.accessor] as React.ReactNode)}
                  </td>
                ))}
                <td className="px-8 py-6 text-right">
                   <button className="p-2 hover:bg-white/10 rounded-lg text-slate-600 group-hover:text-slate-300 transition-all">
                      <MoreVertical className="w-4 h-4" />
                   </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Card View */}
      <div className="lg:hidden space-y-4">
        {data.map((item) => (
          <div 
            key={item.id}
            onClick={() => onRowClick?.(item)}
            className="p-6 bg-white/[0.02] border border-white/5 rounded-[32px] space-y-4 active:scale-95 transition-all shadow-xl"
          >
            <div className="flex items-center justify-between">
              <div className="font-bold text-white text-lg">
                {/* Find the first column as title if it's a string, or use id */}
                {typeof columns[0].accessor === 'function' 
                  ? columns[0].accessor(item) 
                  : (item[columns[0].accessor] as React.ReactNode)}
              </div>
              <button className="p-2 text-slate-600">
                <MoreVertical className="w-4 h-4" />
              </button>
            </div>
            
            <div className="grid grid-cols-1 gap-4">
              {columns.slice(1).map((col, i) => (
                <div key={i} className="space-y-1">
                  <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest">{col.header}</div>
                  <div className="text-sm font-medium text-slate-300">
                    {typeof col.accessor === 'function' 
                      ? col.accessor(item) 
                      : (item[col.accessor] as React.ReactNode)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
