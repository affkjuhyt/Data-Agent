import React, { useEffect, useMemo, useState } from 'react';
import {
  BookMarked, Table2, Search, Loader2, Calendar, Hash, GitBranch, FileText, Database,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiGet } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { KnowledgeDocument } from '../../../types';

/** Mirrors services/workflow-runtime/routers/schema_catalog.py's _serialize(). */
interface SchemaCatalogEntry {
  id: string;
  warehouse_id: string;
  schema_name: string;
  table_name: string;
  columns: Array<{ name: string; type: string; nullable?: boolean; comment?: string | null }>;
  row_count: number | null;
  last_altered: string | null;
  text_description: string | null;
  join_hints: string[];
  schema_hash: string;
  last_crawled_at: string;
}

const formatRowCount = (n: number | null) => (n == null ? '—' : n.toLocaleString());
const formatDate = (iso: string | null) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
};

export const SchemaCatalogView: React.FC = () => {
  // First page of warehouses is enough for a filter dropdown + id→name lookup
  // (same endpoint WarehouseCenter.tsx uses) — this view doesn't manage
  // warehouses itself, just needs their display names.
  const { items: warehouses } = useCursorPagination<KnowledgeDocument>('/api/knowledge/warehouses', {}, []);
  const warehouseName = useMemo(
    () => new Map(warehouses.map(w => [w.data_id, w.name])),
    [warehouses],
  );

  const [warehouseFilter, setWarehouseFilter] = useState('');
  const [search, setSearch] = useState('');
  const [entries, setEntries] = useState<SchemaCatalogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selected, setSelected] = useState<SchemaCatalogEntry | null>(null);

  useEffect(() => {
    setIsLoading(true);
    const params = warehouseFilter ? { warehouse_id: warehouseFilter } : {};
    apiGet<SchemaCatalogEntry[]>('/api/schema-catalog', params)
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setIsLoading(false));
  }, [warehouseFilter]);

  // Server side only supports warehouse_id/schema_name filters — table name /
  // description search is done client-side over the already-fetched page.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(e =>
      e.table_name.toLowerCase().includes(q)
      || e.schema_name.toLowerCase().includes(q)
      || (e.text_description ?? '').toLowerCase().includes(q));
  }, [entries, search]);

  return (
    <div className="animate-in fade-in duration-300 space-y-4">
      {/* header row */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <BookMarked className="w-4 h-4 text-[#B88719]" />
          <h3 className="text-sm font-bold text-[#1E1B15]">Schema Catalog</h3>
          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]/30">
            {filtered.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={warehouseFilter}
            onChange={e => setWarehouseFilter(e.target.value)}
            className="text-xs border border-[#BFA66A]/30 rounded-lg px-3 py-2 bg-white font-mono text-[#1E1B15]"
          >
            <option value="">All warehouses</option>
            {warehouses.map(w => (
              <option key={w.data_id} value={w.data_id}>{w.name}</option>
            ))}
          </select>
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-[#8A5A00]" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search table, schema, description…"
              className="text-xs border border-[#BFA66A]/30 rounded-lg pl-8 pr-3 py-2 bg-white font-mono w-64 text-[#1E1B15]"
            />
          </div>
        </div>
      </div>

      {/* table */}
      <div className="bg-white border border-[#BFA66A]/20 rounded-2xl overflow-hidden shadow-sm">
        <table className="w-full text-left">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-black border-b border-[#BFA66A]/20 bg-[#FCFBF7]">
              <th className="px-6 py-5">Table</th>
              <th className="px-6 py-5">Warehouse</th>
              <th className="px-6 py-5">Rows</th>
              <th className="px-6 py-5">Last Crawled</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A]/10 text-[#111111]">
            {isLoading ? (
              <tr><td colSpan={4} className="px-8 py-12 text-center bg-white">
                <div className="flex items-center justify-center gap-2 text-slate-400 text-xs font-mono">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              </td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={4} className="px-8 py-12 text-center text-slate-500 text-xs font-mono bg-white">
                No tables in the schema catalog yet — schema-crawler-worker populates this after its first crawl.
              </td></tr>
            ) : filtered.map(entry => (
              <tr
                key={entry.id}
                className="group transition-colors cursor-pointer bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/60"
                onClick={() => setSelected(entry)}
              >
                <td className="px-6 py-6">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center border bg-[#FFFDF8] border-[#BFA66A]/30 text-[#B88719]">
                      <Table2 className="w-5 h-5" />
                    </div>
                    <div>
                      <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors font-mono">
                        {entry.schema_name}.{entry.table_name}
                      </div>
                      {entry.text_description && (
                        <div className="text-[11px] text-slate-500 truncate max-w-[420px]">
                          {entry.text_description.split('\n')[0]}
                        </div>
                      )}
                    </div>
                  </div>
                </td>
                <td className="px-6 py-6 font-mono text-xs text-slate-600">
                  {warehouseName.get(entry.warehouse_id) ?? entry.warehouse_id}
                </td>
                <td className="px-6 py-6 font-mono text-xs text-slate-500">{formatRowCount(entry.row_count)}</td>
                <td className="px-6 py-6 font-mono text-xs text-slate-500">{formatDate(entry.last_crawled_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Detail drawer ── */}
      <DetailDrawer
        isOpen={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.schema_name}.${selected.table_name}` : 'Table'}
        subtitle={selected ? (warehouseName.get(selected.warehouse_id) ?? selected.warehouse_id) : ''}
        icon={Table2}
        size="wide"
        persistKey="kb-schema-catalog-detail"
      >
        {selected && (
          <div className="p-6 space-y-6">
            <div className="grid grid-cols-3 gap-3 text-xs">
              <div className="bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold flex items-center gap-1"><Database className="w-3 h-3" /> Rows</span>
                <span className="font-mono text-[#1E1B15]">{formatRowCount(selected.row_count)}</span>
              </div>
              <div className="bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold flex items-center gap-1"><Calendar className="w-3 h-3" /> Last Altered</span>
                <span className="font-mono text-[#1E1B15]">{formatDate(selected.last_altered)}</span>
              </div>
              <div className="bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold flex items-center gap-1"><Hash className="w-3 h-3" /> Schema Hash</span>
                <span className="font-mono text-[#1E1B15] truncate block" title={selected.schema_hash}>{selected.schema_hash.slice(0, 12)}…</span>
              </div>
            </div>

            {selected.text_description && (
              <div>
                <h5 className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] mb-2 flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5" /> Description
                </h5>
                <pre className="whitespace-pre-wrap text-xs font-sans text-[#1E1B15] bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-3">
                  {selected.text_description}
                </pre>
              </div>
            )}

            {selected.join_hints.length > 0 && (
              <div>
                <h5 className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] mb-2 flex items-center gap-1.5">
                  <GitBranch className="w-3.5 h-3.5" /> Join Hints
                </h5>
                <div className="flex flex-wrap gap-1.5">
                  {selected.join_hints.map((hint, i) => (
                    <span key={i} className="text-[11px] font-mono bg-[#FFF9E8] text-[#8A5A00] border border-[#BFA66A]/30 rounded px-2 py-1">
                      {hint}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div>
              <h5 className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] mb-2">
                Columns ({selected.columns.length})
              </h5>
              <div className="border border-[#BFA66A]/15 rounded-lg overflow-hidden">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold bg-[#FCFBF7] border-b border-[#BFA66A]/15">
                      <th className="px-3 py-2">Name</th>
                      <th className="px-3 py-2">Type</th>
                      <th className="px-3 py-2">Nullable</th>
                      <th className="px-3 py-2">Comment</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#BFA66A]/10">
                    {selected.columns.map(col => (
                      <tr key={col.name} className="bg-white">
                        <td className="px-3 py-2 font-mono text-[#1E1B15]">{col.name}</td>
                        <td className="px-3 py-2 font-mono text-slate-600">{col.type}</td>
                        <td className="px-3 py-2 font-mono text-slate-500">{col.nullable ? 'YES' : 'NO'}</td>
                        <td className="px-3 py-2 text-slate-500 truncate max-w-[200px]" title={col.comment ?? ''}>
                          {col.comment || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </DetailDrawer>
    </div>
  );
};
