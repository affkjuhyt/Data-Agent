import React, { useState, useEffect, useCallback } from 'react';
import {
  Trash2, RotateCcw, FileText, Layers, GitBranch, AlertTriangle, RefreshCw, Loader2,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiGet, apiMutate } from '../../../lib/apiClient';

/* ─── 3 trash levels (mirror trash_ops.LEVELS) ──────────────────────────────── */
type TrashLevel = 'document' | 'chunk' | 'version';

interface TrashResponse { level: TrashLevel; items: any[]; total: number; }

const MODES: { id: TrashLevel; label: string; icon: typeof FileText; blurb: string }[] = [
  { id: 'document', label: 'Data',           icon: FileText,  blurb: 'Deleted documents (KBData). Restoring re-enables every chunk + its vectors.' },
  { id: 'chunk',    label: 'Chunks',         icon: Layers,    blurb: 'Deleted chunks (KBTextBlock). Restoring re-enables the chunk and its Qdrant point.' },
  { id: 'version',  label: 'Chunk Versions', icon: GitBranch, blurb: 'Deleted chunk versions (KBTextBlockVersion). Postgres-only — vectors are per-chunk.' },
];

export const TrashCenter: React.FC = () => {
  const [mode, setMode] = useState<TrashLevel>('document');
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pendingFlush, setPendingFlush] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((level: TrashLevel) => {
    setLoading(true);
    setError(null);
    setPendingFlush(null);
    apiGet<TrashResponse>(`/api/knowledge/trash/${level}`)
      .then(res => setItems(res?.items ?? []))
      .catch(e => { setItems([]); setError(e?.message || 'Failed to load trash'); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(mode); }, [mode, load]);

  const restore = async (id: string) => {
    setBusyId(id);
    try {
      await apiMutate('POST', `/api/knowledge/trash/${mode}/${id}/restore`);
      setItems(prev => prev.filter(it => (it.id ?? it.data_id) !== id));
    } catch { /* ForbiddenToast handles 403 */ } finally { setBusyId(null); }
  };

  const flush = async (id: string) => {
    setBusyId(id);
    try {
      await apiMutate('DELETE', `/api/knowledge/trash/${mode}/${id}`);
      setItems(prev => prev.filter(it => (it.id ?? it.data_id) !== id));
    } catch { /* ForbiddenToast handles 403 */ } finally { setBusyId(null); setPendingFlush(null); }
  };

  const activeMode = MODES.find(m => m.id === mode)!;

  /* per-level row content */
  const rowTitle = (it: any): string =>
    mode === 'document' ? (it.name || '(unnamed)')
    : mode === 'chunk'  ? `Chunk #${(it.block_index ?? 0) + 1}`
    :                     `Version ${it.version_number}`;

  const rowSub = (it: any): string =>
    mode === 'document' ? `${String(it.current_tier || '').toUpperCase()} · ${it.metadata?.doc_type || it.source_type || ''}`
    : mode === 'chunk'  ? `${it.doc_name || ''} · chunk ${String(it.block_id).slice(0, 8)}`
    :                     `${it.doc_name || ''}${it.is_active ? ' · was ACTIVE' : ''} · ${String(it.block_id).slice(0, 8)}`;

  const rowPreview = (it: any): string =>
    mode === 'document' ? (it.abstract || '')
    : (it.preview || '');

  const idOf = (it: any): string => it.id ?? it.data_id;

  return (
    <div className="animate-in fade-in duration-300">
      {/* Header */}
      <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#111111] font-display flex items-center gap-2">
            <Trash2 className="w-5 h-5 text-[#B88719]" /> Recycle Bin
          </h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl">
            Soft-deleted items live here until you <strong>restore</strong> them (reverses the
            delete tag in Postgres <em>and</em> Qdrant) or <strong>permanently delete</strong> them.
          </p>
        </div>
        <button
          onClick={() => load(mode)}
          className="px-3 py-1.5 rounded-xl text-[11px] font-bold border border-[#BFA66A]/30 bg-white text-[#8A5A00] hover:bg-[#FAF6ED] flex items-center gap-1.5"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} /> Refresh
        </button>
      </div>

      {/* Mode segmented control */}
      <div className="inline-flex rounded-2xl border border-[#BFA66A]/30 bg-white p-1 mb-2 shadow-2xs">
        {MODES.map(m => (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className={cn(
              'px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-all',
              mode === m.id ? 'bg-[#B88719] text-white shadow-sm' : 'text-slate-600 hover:bg-[#FAF6ED]'
            )}
          >
            <m.icon className="w-3.5 h-3.5" /> {m.label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-slate-400 mb-5">{activeMode.blurb}</p>

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-slate-400 text-sm gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading trash…
        </div>
      ) : error ? (
        <div className="py-16 text-center text-sm text-red-500">{error}</div>
      ) : items.length === 0 ? (
        <div className="py-20 text-center">
          <activeMode.icon className="w-10 h-10 text-[#BFA66A]/50 mx-auto mb-3" />
          <p className="text-sm text-slate-400 font-medium">No deleted {activeMode.label.toLowerCase()} here.</p>
          <p className="text-[11px] text-slate-400 mt-1">Items you delete will appear in this bin.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {items.map(it => {
            const id = idOf(it);
            const isBusy = busyId === id;
            const confirming = pendingFlush === id;
            return (
              <div
                key={id}
                className="group flex items-center gap-4 px-4 py-3 rounded-2xl border border-[#E8DEC8] bg-white hover:border-[#B88719]/40 hover:shadow-2xs transition-all"
              >
                <div className="w-9 h-9 rounded-xl bg-[#FAF6ED] border border-[#BFA66A]/25 flex items-center justify-center shrink-0">
                  <activeMode.icon className="w-4 h-4 text-[#B88719]" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold text-[#111111] truncate">{rowTitle(it)}</div>
                  <div className="text-[11px] text-slate-500 truncate font-mono">{rowSub(it)}</div>
                  {rowPreview(it) && (
                    <div className="text-[11px] text-slate-400 truncate mt-0.5">{rowPreview(it)}</div>
                  )}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => restore(id)}
                    disabled={isBusy}
                    className="px-3 py-1.5 rounded-xl text-[11px] font-bold border border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                    Restore
                  </button>
                  {confirming ? (
                    <button
                      onClick={() => flush(id)}
                      disabled={isBusy}
                      className="px-3 py-1.5 rounded-xl text-[11px] font-black border border-red-400 bg-red-600 text-white hover:bg-red-700 flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <AlertTriangle className="w-3.5 h-3.5" /> Confirm delete
                    </button>
                  ) : (
                    <button
                      onClick={() => setPendingFlush(id)}
                      disabled={isBusy}
                      className="px-3 py-1.5 rounded-xl text-[11px] font-bold border border-red-200 bg-red-50 text-red-600 hover:bg-red-100 flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> Delete forever
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default TrashCenter;
