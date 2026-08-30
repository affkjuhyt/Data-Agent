import React, { useState, useEffect, useRef } from 'react';
import {
  Database,
  Search,
  Zap,
  ArrowRight,
  Edit2,
  CheckCircle2,
  Layers,
  Trash2,
  X,
  AlertCircle,
  Eye,
  Loader2,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { KnowledgeDocument } from '../../../types';
import { useAppState } from '../../../AppStateContext';
import { apiMutate } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { SourceCategory, SOURCE_CATEGORIES, ALL_SOURCE_CATEGORIES, sourceParam } from './inventory.data';

interface AssetInventoryProps {
  onSelectAsset: (asset: KnowledgeDocument) => void;
  onPromote?: (doc: KnowledgeDocument) => void;
  promotingIds?: Set<string>;
}

export const AssetInventory = ({ onSelectAsset, onPromote, promotingIds = new Set() }: AssetInventoryProps) => {
  const { updateDocument, deleteDocument } = useAppState();
  const [activeFilter, setActiveFilter] = useState<'bronze' | 'silver' | 'gold'>('bronze');
  // Multi-select source chips, default all-on. Backend does the filtering
  // (`?source=…` → WHERE source_type IN (...)), so this drives the query, not a
  // client-side filter. No "All" chip — all-on already means everything.
  const [selectedSources, setSelectedSources] = useState<Set<SourceCategory>>(
    () => new Set(ALL_SOURCE_CATEGORIES),
  );
  const sourceQuery = sourceParam(selectedSources);
  const [searchQuery, setSearchQuery] = useState('');
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [showVerification, setShowVerification] = useState(false);
  const [isBatchProcessing, setIsBatchProcessing] = useState(false);

  // Server-side cursor pagination — stateless; the client holds the cursor token.
  // Switching the layer tab (activeFilter) resets to page 1 and clears the cache.
  const {
    items: serverDocs, page, totalPages, hasNext, isLoading: isFetching,
    canJump, goTo, reload,
  } = useCursorPagination<KnowledgeDocument>(
    '/api/data/documents',
    { tier: activeFilter, source: sourceQuery },
    [activeFilter, sourceQuery],
  );

  // The promote button only fires off the async worker pipeline; the worker flips
  // `current_tier` in Postgres a beat after the UI's progress bar. Silently re-sync the
  // current page while a promotion is in flight (and shortly after) so the row moves.
  const promotingCount = promotingIds.size;
  const prevPromotingCount = useRef(promotingCount);
  useEffect(() => {
    const finished = promotingCount < prevPromotingCount.current;
    prevPromotingCount.current = promotingCount;

    const timers: ReturnType<typeof setTimeout>[] = [];
    let poll: ReturnType<typeof setInterval> | undefined;
    if (promotingCount > 0) {
      poll = setInterval(() => reload(true), 3000);
    }
    if (finished) {
      [1500, 5000, 12000].forEach(d => timers.push(setTimeout(() => reload(true), d)));
    }
    return () => {
      if (poll) clearInterval(poll);
      timers.forEach(clearTimeout);
    };
  }, [promotingCount, reload]);

  const handleFilterChange = (filter: 'bronze' | 'silver' | 'gold') => {
    setActiveFilter(filter);
    setSelectedIds([]);
  };

  const toggleSource = (cat: SourceCategory) => {
    setSelectedSources(prev => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
    setSelectedIds([]);
  };

  const handleSearchChange = (query: string) => {
    setSearchQuery(query);
  };

  // Source filtering is server-side now (the `source` query param). Only the free-text
  // search stays client-side, scoped to the current server page.
  const filteredDocs = serverDocs.filter(doc => {
    const authorText = doc.metadata?.author ?? doc.added_by ?? '';
    return !searchQuery ||
      doc.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      doc.data_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      authorText.toLowerCase().includes(searchQuery.toLowerCase());
  });

  const handleProcess = async (e: React.MouseEvent, doc: KnowledgeDocument) => {
    e.stopPropagation();
    // Lock: ignore a promote request while this document is already promoting.
    if (promotingIds.has(doc.data_id) || processingId === doc.data_id) return;
    const targetLayer = doc.current_tier === 'bronze' ? 'silver' : 'gold';
    setProcessingId(doc.data_id);
    try {
      await apiMutate('POST', `/api/pipeline/ingestion/promote/${doc.data_id}/${targetLayer}`);
      // Update status only — tier stays the same until pipeline completes
      updateDocument(doc.data_id, { status: targetLayer === 'silver' ? 'CHUNKING' : 'GRAPH_EXTRACTING' });
      setSelectedIds(prev => prev.filter(id => id !== doc.data_id));
      onPromote?.(doc);
    } catch { /* ForbiddenToast handles 403 */ }
    setProcessingId(null);
  };

  const handleDelete = async (e: React.MouseEvent, doc: KnowledgeDocument) => {
    e.stopPropagation();
    try {
      // Warehouses live in their own tables (KBWarehouse), not KBData, so they
      // have a dedicated delete route — the document endpoint can't remove them.
      const isWarehouse = doc.metadata?.doc_type?.startsWith('Warehouse/');
      const path = isWarehouse
        ? `/api/knowledge/warehouses/${doc.data_id}`
        : `/api/data/documents/${doc.data_id}`;
      await apiMutate('DELETE', path);
      deleteDocument(doc.data_id);
      setSelectedIds(prev => prev.filter(id => id !== doc.data_id));
      reload(true); // refresh the current server page so the row clears + backfills
    } catch { /* ForbiddenToast handles 403 */ }
    setPendingDeleteId(null);
  };

  const toggleSelect = (e: React.MouseEvent | React.ChangeEvent, id: string) => {
    e.stopPropagation();
    setSelectedIds(prev => 
      prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = (e: React.ChangeEvent<HTMLInputElement>) => {
    const visibleIds = filteredDocs.map(d => d.data_id);
    if (e.target.checked) {
      setSelectedIds(prev => Array.from(new Set([...prev, ...visibleIds])));
    } else {
      setSelectedIds(prev => prev.filter(id => !visibleIds.includes(id)));
    }
  };

  const handleBatchProcess = async () => {
    setIsBatchProcessing(true);
    let anyProcessed = false;
    for (const id of selectedIds) {
      const doc = serverDocs.find(d => d.data_id === id);
      if (!doc) continue;
      if (promotingIds.has(id)) continue; // already promoting — don't re-queue

      const targetLayer = doc.current_tier === 'bronze' ? 'silver' : 'gold';
      try {
        await apiMutate('POST', `/api/pipeline/ingestion/promote/${id}/${targetLayer}`);
        updateDocument(id, { status: targetLayer === 'silver' ? 'CHUNKING' : 'GRAPH_EXTRACTING' });
        onPromote?.(doc);
        anyProcessed = true;
      } catch { break; }
    }
    setIsBatchProcessing(false);
    if (anyProcessed) {
      setSelectedIds([]);
      setShowVerification(false);
    }
  };

  const handleRemoveFromBatch = (id: string) => {
    setSelectedIds(prev => prev.filter(item => item !== id));
  };

  const selectedDocs = serverDocs.filter(d => selectedIds.includes(d.data_id));

  return (
    <div className="space-y-6 h-full">
      {/* ─── FILTERS PLACED DIRECTLY ABOVE SEARCH BAR ─── */}
      <div className="bg-[#FFFDF8] border border-[#BFA66A] p-5 rounded-2xl flex flex-col gap-4">
         <div className="flex flex-col gap-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] opacity-80 font-mono">
               Data Layers Filter
            </span>
            <div className="flex gap-2.5">
               {(['bronze', 'silver', 'gold'] as const).map(filter => (
                 <button
                   key={filter}
                   onClick={() => handleFilterChange(filter)}
                   className={cn(
                     "px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all cursor-pointer",
                     filter === activeFilter
                       ? filter === 'bronze'
                         ? "bg-[#92620A] text-white shadow-md shadow-[#92620A]/20"
                         : filter === 'silver'
                           ? "bg-[#4A5568] text-white shadow-md shadow-[#4A5568]/20"
                           : "bg-[#B88719] text-white shadow-md shadow-[#B88719]/20"
                       : "bg-white border border-[#BFA66A] text-[#8A5A00] hover:bg-[#FFF9E8]/20"
                   )}
                 >
                   {filter === 'bronze' ? 'Bronze Data Layer' : filter === 'silver' ? 'Silver Data Layer' : 'Gold Data Layer'}
                 </button>
               ))}
            </div>
         </div>
         
         {/* Source type filter */}
         <div className="flex flex-col gap-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] opacity-80 font-mono">
               Source Type
            </span>
            <div className="flex gap-2 flex-wrap">
               {SOURCE_CATEGORIES.map(({ id, label }) => {
                 const active = selectedSources.has(id);
                 return (
                   <button
                     key={id}
                     onClick={() => toggleSource(id)}
                     aria-pressed={active}
                     className={cn(
                       "px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all cursor-pointer",
                       active
                         ? "bg-[#1E1B15] text-[#D9B86C] border border-[#BFA66A] shadow-sm"
                         : "bg-white border border-[#BFA66A] text-[#8A5A00] opacity-60 hover:opacity-100 hover:bg-[#FFF9E8]/40"
                     )}
                   >
                     {label}
                   </button>
                 );
               })}
            </div>
         </div>

         <div className="border-t border-[#BFA66A] pt-3.5 flex items-center">
            <div className="relative group w-full sm:w-80">
               <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A5A00] opacity-65 group-focus-within:opacity-100 transition-opacity" />
               <input 
                 type="text" 
                 placeholder="Search by name, ID or author..." 
                 value={searchQuery}
                 onChange={(e) => handleSearchChange(e.target.value)}
                 className="ui-input pl-12 pr-6 py-2.5 text-xs w-full font-mono" 
               />
            </div>
         </div>
      </div>

      {/* ─── BATCH PROCESS ACTION BAR PLACED ABOVE TABLE ─── */}
      {activeFilter !== 'gold' && (
        <div className="content-card content-card--static flex flex-col sm:flex-row justify-between items-start sm:items-center p-4 gap-3 shadow-2xs">
           <div className="flex flex-col gap-1">
              <span className="text-xs font-bold text-[#111111]">Batch Operations Configurator</span>
              <span className="text-[10px] font-mono text-[#5F5F5F]">
                 Select multiple document rows using checkboxes to execute massive sequence migrations.
              </span>
           </div>
           <div>
              <button
                onClick={() => {
                  if (selectedIds.length > 0) {
                    setShowVerification(true);
                  }
                }}
                disabled={selectedIds.length === 0}
                className={cn(
                  "px-5 py-2.5 rounded-xl text-[11px] font-black uppercase tracking-wider transition-all flex items-center gap-2 cursor-pointer",
                  selectedIds.length > 0
                    ? "bg-[#1E1B15] text-[#FAFAFA] border border-[#BFA66A] hover:bg-[#2C271E] shadow-sm transform hover:-translate-y-0.5 active:translate-y-0"
                    : "bg-[#FFF9E8] border border-[#BFA66A] text-[#5F5F5F] cursor-not-allowed"
                )}
              >
                <Layers className="w-3.5 h-3.5 text-[#D9B86C]" />
                <span>Multiple Process</span>
                <span className="bg-[#BFA66A] text-[#111111] font-mono text-[9px] w-4.5 h-4.5 flex items-center justify-center rounded-full font-black">
                   {selectedIds.length}
                </span>
              </button>
           </div>
        </div>
      )}

      {/* ─── DATA TABLE ─── */}
      <div className="content-card content-card--static overflow-hidden">
         <table className="w-full text-left border-collapse">
            <thead>
               <tr className="bg-[#FFFDF8] text-[10px] font-black text-[#5A4209] uppercase tracking-widest border-b border-[#BFA66A]">
                  {activeFilter !== 'gold' && (
                    <th className="px-6 py-5 w-12 text-center">
                       <input
                         type="checkbox"
                         checked={filteredDocs.length > 0 && filteredDocs.every(d => selectedIds.includes(d.data_id))}
                         onChange={toggleSelectAll}
                         className="w-4 h-4 rounded border-[#BFA66A] text-[#B88719] focus:ring-[#B88719] cursor-pointer"
                       />
                    </th>
                  )}
                  <th className="px-6 py-5">Document Name</th>
                  <th className="px-6 py-5">Source_type</th>
                  <th className="px-6 py-5">Added_date</th>
                  <th className="px-6 py-5">language</th>
                  <th className="px-8 py-5 text-right font-black">action</th>
               </tr>
            </thead>
            <tbody className="divide-y divide-[#BFA66A] text-[#111111]">
               {isFetching ? (
                 <tr>
                    <td colSpan={activeFilter !== 'gold' ? 6 : 5} className="px-8 py-12 text-center bg-white">
                      <div className="flex items-center justify-center gap-2 text-[#5F5F5F] text-xs font-mono">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Loading…
                      </div>
                    </td>
                 </tr>
               ) : filteredDocs.length === 0 ? (
                 <tr>
                    <td colSpan={activeFilter !== 'gold' ? 6 : 5} className="px-8 py-12 text-center text-[#5F5F5F] text-xs font-mono bg-white">
                      No matching documents in {activeFilter.toUpperCase()} layer.
                    </td>
                 </tr>
               ) : (
                 filteredDocs.map((doc) => (
                    <tr
                      key={doc.data_id}
                      className={cn(
                        "group transition-colors cursor-pointer",
                        promotingIds.has(doc.data_id)
                          ? "bg-amber-50/60 animate-pulse"
                          : selectedIds.includes(doc.data_id)
                            ? "bg-[#FDE8A0]/40"
                            : doc.current_tier === 'bronze'
                              ? "bg-[#FDF0CC]/50 hover:bg-[#FADB88]/30"
                              : doc.current_tier === 'silver'
                                ? "bg-[#F4F6F9]/60 hover:bg-[#E8EDF4]/60"
                                : "bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/50"
                      )}
                      onClick={() => onSelectAsset(doc)}
                    >
                       {activeFilter !== 'gold' && (
                         <td className="px-6 py-6 w-12 text-center" onClick={(e) => e.stopPropagation()}>
                            <input 
                              type="checkbox" 
                              checked={selectedIds.includes(doc.data_id)}
                              onChange={(e) => toggleSelect(e, doc.data_id)}
                              className="w-4 h-4 rounded border-[#BFA66A] text-[#B88719] focus:ring-[#B88719] cursor-pointer"
                            />
                         </td>
                       )}
                       <td className="px-6 py-6">
                          <div className="flex items-center gap-4">
                             <div className={cn(
                               "w-10 h-10 rounded-xl flex items-center justify-center border",
                               doc.current_tier === 'bronze' ? 'bg-[#FFF9E8] border-[#BFA66A] text-[#8A5A00]' :
                               doc.current_tier === 'silver' ? 'bg-[#FFF9E8] border-[#BFA66A] text-[#5F5F5F]' :
                               'bg-[#FFFDF8] border-[#BFA66A] text-[#B88719]'
                             )}>
                                {doc.current_tier === 'bronze' ? <Database className="w-5 h-5" /> : 
                                 doc.current_tier === 'silver' ? <Edit2 className="w-5 h-5" /> : 
                                 <Zap className="w-5 h-5 animate-pulse" />}
                             </div>
                             <div>
                                <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors uppercase tracking-tight">{doc.name}</div>
                                <div className="text-[10px] text-[#5F5F5F] mt-0.5">Added by {doc.metadata?.author ?? doc.added_by ?? '—'}</div>
                             </div>
                          </div>
                       </td>
                       <td className="px-6 py-6 font-mono text-xs text-[#3F3F3F]">
                          {doc.metadata.doc_type || 'Document'}
                       </td>
                       <td className="px-6 py-6 font-mono text-xs text-[#5F5F5F]">{doc.added_on}</td>
                       <td className="px-6 py-6 font-mono text-xs text-[#3F3F3F]">
                          {doc.metadata.language || 'English'}
                       </td>
                       <td className="px-8 py-6 flex items-center justify-end gap-2.5 text-right font-bold" onClick={(e) => e.stopPropagation()}>

                             <button
                               onClick={() => onSelectAsset(doc)}
                               className="btn-ghost btn-sm mr-1"
                               title="Inspect Asset"
                             >
                               <Eye className="w-3.5 h-3.5" />
                               <span>Inspect</span>
                             </button>
                          {pendingDeleteId === doc.data_id ? (
                            <span className="inline-flex items-center gap-1.5">
                              <button
                                onClick={(e) => handleDelete(e, doc)}
                                className="px-2.5 py-1.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all inline-flex items-center gap-1 cursor-pointer"
                              >Confirm</button>
                              <button
                                onClick={(e) => { e.stopPropagation(); setPendingDeleteId(null); }}
                                className="p-1.5 bg-white border border-[#BFA66A] hover:bg-[#FFF9E8] text-[#5F5F5F] rounded-lg transition-colors cursor-pointer"
                              ><X className="w-3 h-3" /></button>
                            </span>
                          ) : (
                            <button
                              onClick={(e) => { e.stopPropagation(); setPendingDeleteId(doc.data_id); }}
                              className="p-1.5 opacity-0 group-hover:opacity-100 bg-red-50 hover:bg-red-100 border border-red-200 text-red-500 rounded-lg transition-all cursor-pointer"
                              title="Delete document"
                            ><Trash2 className="w-3.5 h-3.5" /></button>
                          )}
                          {doc.metadata?.doc_type?.startsWith('Warehouse/') ? (
                            <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-500/10 border border-blue-500/20 rounded-lg text-blue-700">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              <span className="text-[10px] font-mono font-black uppercase tracking-wider">Warehouse</span>
                            </div>
                          ) : (processingId === doc.data_id || promotingIds.has(doc.data_id)) ? (
                            <span className="text-[10px] font-mono font-bold text-[#B88719] uppercase tracking-wide flex items-center justify-end gap-1.5 font-mono">
                              <span className="w-3 h-3 rounded-full border-2 border-[#B88719] border-t-[#B88719] animate-spin" />
                              Processing...
                            </span>
                          ) : doc.current_tier === 'bronze' ? (
                            <button
                              onClick={(e) => handleProcess(e, doc)}
                              className="btn-ghost btn-sm"
                            >
                              Process to Silver
                              <ArrowRight className="w-3 h-3" />
                            </button>
                          ) : doc.current_tier === 'silver' ? (
                            <button
                              onClick={(e) => handleProcess(e, doc)}
                              className="btn-ghost btn-sm"
                            >
                              Process to Gold
                              <ArrowRight className="w-3 h-3" />
                            </button>
                          ) : (
                            <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-green-500/10 border border-green-500/20 rounded-lg text-green-700">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              <span className="text-[10px] font-mono font-black uppercase tracking-wider">Ready / Active</span>
                            </div>
                          )}
                       </td>
                    </tr>
                 ))
               )}
            </tbody>
         </table>

         {/* Pagination Footer */}
         {(totalPages > 1 || hasNext) && (
           <div className="flex items-center justify-between gap-4 px-8 py-4 bg-[#FFFDF8] border-t border-[#BFA66A] rounded-b-3xl">
              <span className="text-[11px] font-mono text-[#5F5F5F]">
                Page <span className="font-bold text-[#111111]">{page}</span>
                <span className="text-[#5F5F5F]"> of {totalPages}</span>
                {isFetching && <span className="text-[#5F5F5F]"> · loading…</span>}
              </span>
              <Pagination
                page={page}
                totalPages={totalPages}
                hasNext={hasNext}
                canJump={canJump}
                onJump={goTo}
              />
           </div>
         )}
      </div>

      {/* Verification Dialog Modal overlay */}
      {showVerification && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs select-none">
          <div className="bg-[#13110E] border border-[#BFA66A] rounded-[2rem] w-full max-w-lg shadow-2xl flex flex-col overflow-hidden text-white pointer-events-auto">
            
            {/* Modal Header */}
            <div className="p-6 border-b border-[#BFA66A] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Layers className="w-5 h-5 text-[#D9B86C]" />
                <div>
                  <h3 className="text-sm font-display font-black uppercase tracking-wider text-[#D9B86C]">Batch Pipeline Verification</h3>
                  <p className="text-[10px] font-mono text-[#5F5F5F] mt-0.5">Please confirm items staged for {activeFilter === 'bronze' ? 'SILVER' : 'GOLD'} tier upgrade</p>
                </div>
              </div>
              <button 
                onClick={() => setShowVerification(false)}
                className="p-1.5 hover:bg-white/10 rounded-xl text-slate-400 hover:text-white transition-all cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Modal Body: Active Verification List */}
            <div className="p-6 flex-1 overflow-y-auto max-h-[320px] space-y-3 custom-scrollbar">
              {selectedDocs.length === 0 ? (
                <div className="py-12 text-center text-[#5F5F5F] text-xs font-mono flex flex-col items-center gap-2">
                  <AlertCircle className="w-6 h-6 text-amber-500" />
                  <span>No documents selected. Please add items back or close table select view.</span>
                </div>
              ) : (
                selectedDocs.map((doc) => (
                  <div 
                    key={doc.data_id} 
                    className="flex items-center justify-between p-3.5 bg-white/[0.03] rounded-xl border border-white/15 hover:border-[#BFA66A] transition-all gap-4"
                  >
                    <div className="flex items-center gap-3">
                      {/* REMOVE BUTTON ON THE LEFT */}
                      <button
                        onClick={() => handleRemoveFromBatch(doc.data_id)}
                        aria-label="Remove from selection"
                        className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-500 hover:text-red-400 rounded-lg transition-colors border border-red-500/15 cursor-pointer flex items-center justify-center shrink-0"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                      <div className="min-w-0">
                        <h4 className="text-xs font-bold text-slate-100 uppercase tracking-tight truncate max-w-[240px]">
                          {doc.name}
                        </h4>
                        <span className="text-[9px] font-mono text-[#5F5F5F] uppercase tracking-widest mt-0.5 block">
                          ID: {doc.data_id}
                        </span>
                      </div>
                    </div>
                    
                    {/* Source type label/badge on the right */}
                    <div className="shrink-0 flex items-center gap-2.5">
                      <span className="bg-white/5 border border-white/20 text-slate-300 text-[10px] font-mono px-2.5 py-1 rounded-lg">
                        {doc.metadata.doc_type || 'Document'}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-6 bg-white/[0.01] border-t border-[#BFA66A] flex items-center justify-between gap-3">
              <button
                onClick={() => setShowVerification(false)}
                className="px-4 py-2 bg-transparent hover:bg-white/5 text-slate-300 hover:text-white rounded-xl text-[10px] font-bold uppercase tracking-wider transition-all border border-white/20 cursor-pointer"
              >
                Discard
              </button>
              
              <button
                onClick={handleBatchProcess}
                disabled={selectedDocs.length === 0 || isBatchProcessing}
                className={cn(
                  "px-6 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all inline-flex items-center gap-2 cursor-pointer",
                  selectedDocs.length > 0 && !isBatchProcessing
                    ? "bg-[#D9B86C] text-[#13110E] hover:bg-[#FFE099] shadow-md"
                    : "bg-slate-700 text-slate-400 cursor-not-allowed"
                )}
              >
                {isBatchProcessing ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-[#8A5A00] border-t-white animate-spin" />
                    <span>Ingesting Sequence...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Confirm Batch Process</span>
                  </>
                )}
              </button>
            </div>

          </div>
        </div>
      )}
    </div>
  );
};
