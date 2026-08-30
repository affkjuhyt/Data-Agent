import React, { useState, useEffect } from 'react';
import {
  ShieldAlert,
  Clock,
  CheckCircle2,
  Lock,
  Check,
  Info,
  Search,
  CheckCircle,
  FileText,
  Calendar,
  ChevronRight,
  ChevronDown,
  FolderOpen,
  ArrowLeft,
  Loader2,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import {
  ConflictType, SeverityType, ConflictStatusType, ResolutionMethod,
  ConflictSummary, ConflictBatchGroup, ConflictsResponse, ConflictsBucket, ConflictDetail,
  BatchConflictChoice, CONFLICT_TYPE_LABELS, classifyBatchResolution,
} from './conflicts.data';

function unwrapBucket<T>(raw: ConflictsBucket<T> | T[]): { items: T[]; has_next: boolean } {
  if (Array.isArray(raw)) return { items: raw, has_next: false };
  return { items: raw.items ?? [], has_next: raw.has_next ?? false };
}
import { RESOLUTION_LABELS } from '../../../lib/enums';


export const ConflictWorkspace = () => {
  const [cached, setCached] = useState<ConflictsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  // Per-bucket page state for server-side pagination
  const [pendingPage, setPendingPage]   = useState(1);
  const [awaitingPage, setAwaitingPage] = useState(1);
  const [resolvedPage, setResolvedPage] = useState(1);

  // has_next flags derived from the unwrapped response
  const [pendingHasNext, setPendingHasNext]   = useState(false);
  const [awaitingHasNext, setAwaitingHasNext] = useState(false);
  const [resolvedHasNext, setResolvedHasNext] = useState(false);

  const [activeTab, setActiveTab] = useState<ConflictStatusType>('pending');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [pendingView, setPendingView] = useState<'batches' | 'conflicts'>('batches');

  // Detail drawer state — loaded on row click
  const [selectedConflictId, setSelectedConflictId] = useState<string | null>(null);
  const [conflictDetail, setConflictDetail] = useState<ConflictDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Resolution sandbox — a method is chosen per conflict, then the whole batch is submitted
  const [resolutionInstruction, setResolutionInstruction] = useState('');
  const [selectedMethod, setSelectedMethod] = useState<ResolutionMethod | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Per-conflict choices staged for the currently selected pending batch
  const [batchChoices, setBatchChoices] = useState<Record<string, BatchConflictChoice>>({});

  // Full-text expansion toggles for each snapshot panel
  const [existingExpanded, setExistingExpanded] = useState(false);
  const [incomingExpanded, setIncomingExpanded] = useState(false);

  const loadConflicts = (pp = pendingPage, ap = awaitingPage, rp = resolvedPage) => {
    setLoading(true);
    apiGet<ConflictsResponse>('/api/knowledge/conflicts', {
      pending_page: pp, awaiting_page: ap, resolved_page: rp,
    })
      .then(data => {
        setCached(data);
        const { items: pItems, has_next: pNext } = unwrapBucket(data.pending);
        const { has_next: aNext } = unwrapBucket(data.awaiting);
        const { has_next: rNext } = unwrapBucket(data.resolved);
        setPendingHasNext(pNext);
        setAwaitingHasNext(aNext);
        setResolvedHasNext(rNext);
        if (pp === 1 && pItems.length > 0) {
          const first = pItems[0] as ConflictBatchGroup;
          if (first.batch_id) setSelectedBatchId(first.batch_id);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadConflicts(1, 1, 1); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Reload when page changes
  useEffect(() => { loadConflicts(pendingPage, awaitingPage, resolvedPage); },
    [pendingPage, awaitingPage, resolvedPage]); // eslint-disable-line react-hooks/exhaustive-deps

  // Counters derived from cache
  const pendingBatches = cached ? unwrapBucket(cached.pending).items as ConflictBatchGroup[] : [];
  const awaitingItems  = cached ? unwrapBucket(cached.awaiting).items as ConflictSummary[]   : [];
  const resolvedItems  = cached ? unwrapBucket(cached.resolved).items as ConflictSummary[]   : [];

  const pendingCount  = pendingBatches.reduce((sum, b) => sum + b.number_pending_conflict, 0);
  const awaitingCount = awaitingItems.length;
  const resolvedCount = resolvedItems.length;

  // Summaries for the active list view
  const currentSummaries: ConflictSummary[] = (() => {
    if (!cached) return [];
    if (activeTab === 'pending') {
      const batch = pendingBatches.find(b => b.batch_id === selectedBatchId);
      return batch?.conflicts ?? [];
    }
    if (activeTab === 'awaiting') return awaitingItems;
    return resolvedItems;
  })();

  const filteredSummaries = currentSummaries.filter(c =>
    c.conflict_type.toLowerCase().includes(searchQuery.toLowerCase()) ||
    c.detected_at.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Batch-resolution state for the selected pending batch (a batch is resolved as a whole)
  const selectedBatchConflicts = activeTab === 'pending'
    ? (pendingBatches.find(b => b.batch_id === selectedBatchId)?.conflicts ?? [])
    : [];
  const assignedMethods = selectedBatchConflicts
    .map(c => batchChoices[c.conflict_id]?.method)
    .filter((method): method is ResolutionMethod => Boolean(method));
  const allConflictsAssigned = selectedBatchConflicts.length > 0
    && assignedMethods.length === selectedBatchConflicts.length;
  const batchMode = allConflictsAssigned ? classifyBatchResolution(assignedMethods) : 'invalid';
  const canResolveBatch = allConflictsAssigned && batchMode !== 'invalid';

  const batchValidationMessage = (): string => {
    if (!allConflictsAssigned) {
      return `Assign a strategy to all ${selectedBatchConflicts.length} conflicts to resolve this batch.`;
    }
    if (batchMode === 'all_keep_existing') return 'All conflicts keep the stored version.';
    if (batchMode === 'single_keep_incoming') return 'One incoming version replaces the stored block; the rest keep existing.';
    if (batchMode === 'merge') return 'Merge-marked incoming chunks will be fused into the stored block one by one.';
    return 'Invalid mix: keep-incoming needs exactly one (rest keep-existing); merge cannot combine with keep-incoming.';
  };

  const renderSeverityBadge = (sev: SeverityType) => {
    const styles: Record<SeverityType, string> = {
      high:   'bg-red-500/10 text-red-700 border-red-500/20 px-2 py-0.5 rounded text-[10px] uppercase font-bold border font-mono',
      medium: 'bg-amber-500/10 text-amber-700 border-amber-500/20 px-2 py-0.5 rounded text-[10px] uppercase font-bold border font-mono',
      low:    'bg-slate-100 text-slate-600 border-slate-200 px-2 py-0.5 rounded text-[10px] uppercase font-bold border font-mono',
    };
    return <span className={styles[sev]}>{sev}</span>;
  };

  const isMethodDisabled = (method: ResolutionMethod, type: ConflictType): boolean => {
    if (type === 'content_contradiction' || type === 'table_schema') {
      return method === 'merge' || method === 'no_action';
    }
    if (type === 'content_update') return method === 'merge';
    if (type === 'content_duplicate') return method === 'no_action';
    return false;
  };

  // Row click: store conflict_id for row highlight, fetch full detail
  const handleOpenConflict = (summary: ConflictSummary) => {
    setSelectedConflictId(summary.conflict_id);
    setConflictDetail(null);
    setDetailLoading(true);
    setResolutionInstruction('');
    setSelectedMethod(null);
    setExistingExpanded(false);
    setIncomingExpanded(false);

    apiGet<ConflictDetail>(`/api/knowledge/conflicts/${summary.conflict_id}`)
      .then(detail => {
        setConflictDetail(detail);
        // Prefer a choice already staged for this batch over the stored value
        const staged = batchChoices[summary.conflict_id];
        setResolutionInstruction(staged?.instruction ?? detail.resolution_instruction ?? '');
        setSelectedMethod(staged?.method ?? detail.selected_resolution_method ?? null);
      })
      .catch(() => setSelectedConflictId(null))
      .finally(() => setDetailLoading(false));
  };

  const handleCloseDrawer = () => {
    setSelectedConflictId(null);
    setConflictDetail(null);
    setResolutionInstruction('');
    setSelectedMethod(null);
  };

  // Stage this conflict's chosen method for the batch — no API call until the whole batch is submitted
  const handleSaveChoice = () => {
    if (!selectedConflictId || !selectedMethod) return;
    setBatchChoices(prev => ({
      ...prev,
      [selectedConflictId]: { method: selectedMethod, instruction: resolutionInstruction },
    }));
    handleCloseDrawer();
  };

  // Submit every conflict's chosen method for the selected batch at once
  const handleResolveBatch = async () => {
    const batch = pendingBatches.find(b => b.batch_id === selectedBatchId);
    if (!batch) return;
    const resolutions = batch.conflicts.map(conflict => ({
      conflict_id: conflict.conflict_id,
      selected_resolution_method: batchChoices[conflict.conflict_id].method,
      resolution_instruction: batchChoices[conflict.conflict_id].instruction || undefined,
    }));
    setSubmitting(true);
    try {
      await apiMutate('POST', `/api/knowledge/conflicts/batches/${batch.batch_id}/resolve`, { resolutions });
      setBatchChoices({});
      setPendingPage(1); setAwaitingPage(1); setResolvedPage(1);
      loadConflicts(1, 1, 1);
      setActiveTab('awaiting');
      setPendingView('batches');
      handleCloseDrawer();
    } catch { /* error surfaced by apiMutate */ }
    finally { setSubmitting(false); }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500 text-[#111111]">

      {/* CONFLICT DETAIL DRAWER */}
      <DetailDrawer
        isOpen={!!selectedConflictId}
        onClose={handleCloseDrawer}
        title={conflictDetail ? (CONFLICT_TYPE_LABELS[conflictDetail.conflict_type]?.title ?? conflictDetail.conflict_type) : (detailLoading ? 'Loading...' : 'Conflict Detail')}
        subtitle={conflictDetail ? conflictDetail.status.toUpperCase() : ''}
        icon={ShieldAlert}
        size="wide"
        fixedHeight
        footer={
          conflictDetail?.status === 'pending' ? (
            <div className="flex justify-end gap-2.5 w-full">
              <button
                type="button"
                onClick={handleCloseDrawer}
                className="px-3.5 py-2 bg-white border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-bold text-slate-500 uppercase tracking-wide cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveChoice}
                disabled={!selectedMethod}
                className={cn(
                  "px-5 py-2 rounded-xl text-xs font-black uppercase tracking-wider transition-all flex items-center gap-1.5 cursor-pointer",
                  selectedMethod
                    ? "bg-[#B88719] text-white shadow-md hover:bg-[#8A5A00]"
                    : "bg-slate-100 text-slate-400 border border-slate-200 cursor-not-allowed"
                )}
              >
                <CheckCircle className="w-4 h-4" />
                <span>{selectedConflictId && batchChoices[selectedConflictId] ? 'Update Choice' : 'Save Choice'}</span>
              </button>
            </div>
          ) : (
            <div className="flex justify-end w-full">
              <button
                type="button"
                onClick={handleCloseDrawer}
                className="px-3.5 py-1.5 bg-white border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-bold text-slate-500 uppercase tracking-wide cursor-pointer"
              >
                Close View
              </button>
            </div>
          )
        }
      >
        {detailLoading && (
          <div className="flex items-center justify-center h-40">
            <Loader2 className="w-6 h-6 animate-spin text-[#B88719]" />
          </div>
        )}

        {conflictDetail && (
          <div className="p-5 space-y-5 text-[#111111] bg-white">

            {/* Conflict metadata warning banner */}
            <div className="bg-[#FFFDF8] border border-[#BFA66A]/30 p-4 rounded-xl flex gap-3 shadow-xs">
              <Info className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
              <div className="space-y-0.5">
                <h4 className="text-xs font-bold text-[#2A2A2A] uppercase tracking-wide">
                  {CONFLICT_TYPE_LABELS[conflictDetail.conflict_type].title}
                </h4>
                <p className="text-[11px] text-[#5A5A5A] leading-relaxed">
                  {CONFLICT_TYPE_LABELS[conflictDetail.conflict_type].desc}
                </p>
              </div>
            </div>

            {/* Conflict detailed description */}
            <div className="space-y-1">
              <span className="text-[9px] font-bold uppercase tracking-widest text-[#8A5A00] font-mono">
                Reconciliation Explanation
              </span>
              <p className="text-[11px] text-slate-700 leading-normal bg-[#FAF9F5] p-3.5 rounded-xl border border-[#BFA66A]/15 italic">
                "{conflictDetail.detailed_explanation}"
              </p>
            </div>

            {/* Mini details table */}
            <div className="grid grid-cols-2 gap-3 text-[11px]">
              <div className="p-2.5 bg-slate-50 border border-slate-200/50 rounded-xl">
                <span className="text-[8px] font-mono text-slate-400 font-bold uppercase block">Storage Hub</span>
                <span className="font-bold font-mono text-slate-700 capitalize block truncate">{conflictDetail.where_happens}</span>
              </div>
              <div className="p-2.5 bg-slate-50 border border-slate-200/50 rounded-xl">
                <span className="text-[8px] font-mono text-slate-400 font-bold uppercase block">Affected Location</span>
                <span className="font-bold font-mono text-slate-700 block truncate" title={conflictDetail.affected_location}>
                  {conflictDetail.affected_location}
                </span>
              </div>
            </div>

            {/* Side by side summary diff */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Snapshot A */}
              <div className="flex flex-col gap-0">
                <div className="border border-slate-200 rounded-xl overflow-hidden flex flex-col shadow-xs bg-white">
                  <div className="bg-[#1F1E19] px-3.5 py-2 flex items-center justify-between">
                    <span className="text-[9px] font-mono text-slate-300 font-black tracking-wider uppercase">
                      Snapshot A: Database Canonical
                    </span>
                    <span className="text-[8px] font-bold bg-[#FAF6EA] border border-[#BFA66A]/30 px-1.5 py-0.5 rounded text-[#8A5A00]">
                      Existing Key
                    </span>
                  </div>
                  <div className="p-3.5 bg-[#FAF9F5] flex-1 min-h-[120px]">
                    <p className="text-[11px] text-slate-700 leading-relaxed whitespace-pre-wrap">
                      {(conflictDetail.existing_snapshot as Record<string, string>)?.summary || 'No summary available.'}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setExistingExpanded(v => !v)}
                  className="mt-1.5 flex items-center gap-1 self-start px-2.5 py-1 rounded-lg text-[10px] font-bold text-[#8A5A00] bg-[#FFF9E8] border border-[#BFA66A]/30 hover:bg-[#B88719] hover:text-white transition-all cursor-pointer"
                >
                  <ChevronDown className={cn("w-3 h-3 transition-transform", existingExpanded && "rotate-180")} />
                  {existingExpanded ? 'Hide Full Text' : 'View Full Text'}
                </button>
                {existingExpanded && (
                  <div className="mt-1 border border-[#BFA66A]/20 rounded-xl bg-[#FAF9F5] p-3.5 max-h-64 overflow-y-auto">
                    <p className="text-[11px] text-slate-700 leading-relaxed whitespace-pre-wrap font-mono">
                      {conflictDetail.existing_db_content ?? 'No content available.'}
                    </p>
                  </div>
                )}
              </div>

              {/* Snapshot B */}
              <div className="flex flex-col gap-0">
                <div className="border border-slate-200 rounded-xl overflow-hidden flex flex-col shadow-xs bg-white">
                  <div className="bg-[#1D1B17] px-3.5 py-2 flex items-center justify-between">
                    <span className="text-[9px] font-mono text-slate-300 font-black tracking-wider uppercase">
                      Snapshot B: Ingestion Buffer
                    </span>
                    <span className="text-[8px] font-bold bg-[#F3E2A7] border border-[#B88719]/30 px-1.5 py-0.5 rounded text-[#111111]">
                      Incoming Stream
                    </span>
                  </div>
                  <div className="p-3.5 bg-[#FAF9F5] flex-1 min-h-[120px]">
                    <p className="text-[11px] text-slate-700 leading-relaxed whitespace-pre-wrap">
                      {(conflictDetail.incoming_snapshot as Record<string, string>)?.summary || 'No summary available.'}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setIncomingExpanded(v => !v)}
                  className="mt-1.5 flex items-center gap-1 self-start px-2.5 py-1 rounded-lg text-[10px] font-bold text-[#8A5A00] bg-[#FFF9E8] border border-[#BFA66A]/30 hover:bg-[#B88719] hover:text-white transition-all cursor-pointer"
                >
                  <ChevronDown className={cn("w-3 h-3 transition-transform", incomingExpanded && "rotate-180")} />
                  {incomingExpanded ? 'Hide Full Text' : 'View Full Text'}
                </button>
                {incomingExpanded && (
                  <div className="mt-1 border border-[#BFA66A]/20 rounded-xl bg-[#FAF9F5] p-3.5 max-h-64 overflow-y-auto">
                    <p className="text-[11px] text-slate-700 leading-relaxed whitespace-pre-wrap font-mono">
                      {(conflictDetail.incoming_snapshot as Record<string, string>)?.content || 'No content available.'}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Resolution form / locked archive view */}
            <div className="border-t border-[#BFA66A]/15 pt-4 space-y-4">
              {conflictDetail.status === 'pending' ? (
                <div className="space-y-2">
                  <label className="text-[9px] font-bold uppercase tracking-widest text-[#8A5A00] font-mono block">
                    Choose Reconciliation Action Strategy
                  </label>
                  <p className="text-[10px] text-slate-400 italic">
                    Select one canonical strategy to reconcile these records. Unallowed options are automatically disabled.
                  </p>

                  <div className="grid grid-cols-1 gap-2 pt-1 font-sans">
                    {(
                      [
                        { method: 'keep_existing' as ResolutionMethod, label: 'Keep Stored (Canonical Snapshot A)',         desc: 'Retains local Snapshot A, discarding incoming files completely.' },
                        { method: 'keep_incoming' as ResolutionMethod, label: 'Over-write (Active Snapshot B)',             desc: 'Supersedes database values and commits new ingestion stream raw records directly.' },
                        { method: 'merge'         as ResolutionMethod, label: 'Guided Merge (Coexisting Hybrid Synthesis)', desc: 'Invokes AI-assisted pipeline to fuse Snapshot A and B using your custom prompt instructions.' },
                      ] as const
                    ).map(({ method, label, desc }) => {
                      const disabled = isMethodDisabled(method, conflictDetail.conflict_type);
                      const active = selectedMethod === method;
                      return (
                        <button
                          key={method}
                          type="button"
                          onClick={() => setSelectedMethod(method)}
                          disabled={disabled}
                          className={cn(
                            "p-3 rounded-xl border text-left transition-all flex flex-col gap-1 cursor-pointer",
                            active ? "border-[#B88719] bg-[#FFFBF0] shadow-sm ring-1 ring-[#B88719]"
                                   : "border-slate-200 bg-white hover:bg-slate-50/50",
                            disabled && "opacity-35 cursor-not-allowed bg-slate-50 border-slate-200"
                          )}
                        >
                          <span className="text-xs font-bold text-[#111111] flex items-center gap-1.5">
                            <span className={cn("w-2 h-2 rounded-full", active ? "bg-amber-600 animate-pulse" : "bg-slate-300")} />
                            {label}
                          </span>
                          <span className="text-[10px] text-slate-500 leading-normal block pl-3.5">{desc}</span>
                        </button>
                      );
                    })}
                  </div>

                  <div className="space-y-1.5 pt-1">
                    <label className="text-[9px] font-bold uppercase tracking-widest text-[#8A5A00] font-mono block">
                      AI Directive and Synthesis Prompt Guidelines
                    </label>
                    <textarea
                      value={resolutionInstruction}
                      onChange={(e) => setResolutionInstruction(e.target.value)}
                      placeholder="e.g. Please override EU values with German tax exceptions for region-locked German IPs..."
                      className="w-full h-20 bg-white border border-slate-200 p-3 text-xs text-slate-800 rounded-xl focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719] transition-all font-mono leading-relaxed"
                    />
                  </div>
                </div>
              ) : (
                <div className="bg-[#FAF9F5] p-4 rounded-xl border border-[#BFA66A]/25 space-y-4 text-[11px]">
                  <div className="flex items-center gap-1.5 text-[#8A5A00] font-bold">
                    <Lock className="w-3.5 h-3.5" />
                    <span className="uppercase font-mono text-[9px] tracking-widest">Locked Archive Record</span>
                  </div>

                  <div className="space-y-1">
                    <span className="text-[8px] font-mono text-slate-400 font-bold uppercase tracking-wider block">Completed AI Prompt</span>
                    <p className="text-slate-700 italic border-l-2 border-[#BFA66A]/45 pl-2.5 leading-normal">
                      {conflictDetail.resolution_instruction || 'Standard automated baseline bypass.'}
                    </p>
                  </div>

                  <div className="p-3 bg-white border border-slate-100 rounded-lg space-y-1">
                    <span className="text-[8px] font-mono text-slate-400 font-bold uppercase block">Determined Canonical Action</span>
                    <div className="text-xs font-bold text-[#111111] flex items-center gap-1.5">
                      <CheckCircle2 className="w-4 h-4 text-green-600" />
                      <span>{conflictDetail.selected_resolution_method ? RESOLUTION_LABELS[conflictDetail.selected_resolution_method] : ''}</span>
                    </div>
                  </div>

                  {conflictDetail.status === 'resolved' ? (
                    <div className="pt-2 border-t border-slate-200/60 grid grid-cols-2 gap-2 text-[10px] font-mono text-slate-500">
                      <div>
                        <span>RESOLVED TIME:</span>
                        <span className="block font-bold text-slate-700 mt-0.5">{conflictDetail.resolved_at}</span>
                      </div>
                      <div>
                        <span>AUDITED BY:</span>
                        <span className="block font-bold text-slate-700 mt-0.5">{conflictDetail.resolved_by}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="pt-2 border-t border-slate-200/60 text-purple-700 font-bold">
                      Awaiting batch commit run. Snapshot reconciliation scheduled for worker fleet cycle.
                    </div>
                  )}
                </div>
              )}
            </div>

          </div>
        )}
      </DetailDrawer>

      {/* HEADER SECTION */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
        <div>
          <div className="flex items-center gap-1.5 text-amber-600 text-[10px] font-black tracking-widest uppercase mb-1 font-mono">
            <ShieldAlert className="w-4 h-4 text-amber-500 animate-pulse" />
            Integrity Conflict Workspace
          </div>
          <h1 className="text-3xl font-display font-bold tracking-tight">Conflicts Center</h1>
          <p className="text-slate-500 text-sm mt-1 max-w-2xl">
            Real-time reconciliation of content contradictions, metadata overlaps, and schema exceptions generated during pipeline ingestion.
          </p>
        </div>
      </div>

      {/* METRIC BANNER OVERVIEWS */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
        <div
          onClick={() => { setActiveTab('pending'); setPendingView('batches'); handleCloseDrawer(); }}
          className={cn(
            "bg-white border rounded-2xl p-5 flex items-center justify-between shadow-2xs cursor-pointer transition-all hover:shadow-md",
            activeTab === 'pending' ? "border-[#BFA66A] ring-1 ring-[#BFA66A]" : "border-slate-200"
          )}
        >
          <div>
            <span className="text-[10px] font-mono font-black text-slate-400 uppercase tracking-widest">Pending Action</span>
            <h3 className="text-2xl font-bold font-display text-slate-800 mt-1">{pendingCount}</h3>
          </div>
          <div className="w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-600 flex items-center justify-center font-bold font-mono">P</div>
        </div>

        <div
          onClick={() => { setActiveTab('awaiting'); handleCloseDrawer(); }}
          className={cn(
            "bg-white border rounded-2xl p-5 flex items-center justify-between shadow-xs cursor-pointer transition-all hover:shadow-md",
            activeTab === 'awaiting' ? "border-[#BFA66A] ring-1 ring-[#BFA66A]" : "border-slate-200"
          )}
        >
          <div>
            <span className="text-[10px] font-mono font-black text-slate-400 uppercase tracking-widest">Awaiting AI Sync</span>
            <h3 className="text-2xl font-bold font-display text-slate-800 mt-1">{awaitingCount}</h3>
          </div>
          <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-600 flex items-center justify-center font-bold font-mono font-sans">A</div>
        </div>

        <div
          onClick={() => { setActiveTab('resolved'); handleCloseDrawer(); }}
          className={cn(
            "bg-white border rounded-2xl p-5 flex items-center justify-between shadow-2xs cursor-pointer transition-all hover:shadow-md",
            activeTab === 'resolved' ? "border-[#BFA66A] ring-1 ring-[#BFA66A]" : "border-slate-200"
          )}
        >
          <div>
            <span className="text-[10px] font-mono font-black text-slate-400 uppercase tracking-widest">Resolved Syncs</span>
            <h3 className="text-2xl font-bold font-display text-green-700 mt-1">{resolvedCount}</h3>
          </div>
          <div className="w-10 h-10 rounded-xl bg-green-500/10 border border-green-500/20 text-green-600 flex items-center justify-center font-bold font-mono font-sans">R</div>
        </div>
      </div>

      {/* TIER FILTER BAR & SEARCH */}
      <div className="bg-[#FFFDF8] border border-[#BFA66A]/20 p-5 rounded-2xl flex flex-col gap-4 shadow-sm">
        <div className="flex flex-col gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] opacity-80 font-mono">
            Integrity Status Selection
          </span>
          <div className="flex flex-wrap gap-2.5">
            {(['pending', 'awaiting', 'resolved'] as const).map(tab => {
              const labelMap = {
                pending:  `Pending (${pendingCount})`,
                awaiting: `Awaiting AI (${awaitingCount})`,
                resolved: `Resolved (${resolvedCount})`,
              };
              return (
                <button
                  key={tab}
                  onClick={() => {
                    setActiveTab(tab);
                    handleCloseDrawer();
                    if (tab === 'pending') setPendingView('batches');
                  }}
                  className={cn(
                    "px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all cursor-pointer",
                    tab === activeTab
                      ? "bg-[#B88719] text-white shadow-md shadow-[#B88719]/15"
                      : "bg-white border border-[#BFA66A]/30 text-[#8A5A00] hover:bg-[#FFF9E8]/20"
                  )}
                >
                  {labelMap[tab]}
                </button>
              );
            })}
          </div>
        </div>

        <div className="border-t border-[#BFA66A]/15 pt-3.5 flex items-center">
          <div className="relative group w-full sm:w-80">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A5A00] opacity-65 group-focus-within:opacity-100 transition-opacity" />
            <input
              type="text"
              placeholder="Filter by type or detection date..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-white border border-[#BFA66A]/35 text-[#111111] placeholder:text-slate-400 rounded-2xl pl-12 pr-6 py-2.5 text-xs w-full focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719] transition-all font-mono"
            />
          </div>
        </div>
      </div>

      {/* LAYOUT CONTAINER */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        {/* BATCHES TABLE — pending tab, batches view */}
        {activeTab === 'pending' && pendingView === 'batches' && (
          <div className="lg:col-span-12 space-y-4">
            <div className="border border-[#C49A28]/35 rounded-3xl overflow-hidden bg-[#FDF2CE]/60 shadow-[0_4px_24px_rgba(184,135,25,0.06)]">
              <div className="p-5.5 bg-[#F5E098]/40 border-b border-[#C49A28]/25 flex items-center justify-between">
                <div>
                  <span className="text-[10px] font-mono font-black text-[#5A4209]/80 uppercase tracking-widest block">
                    Ingestion Batches
                  </span>
                  <span className="text-xs text-slate-500 font-medium">
                    Select a batch of ingested documents to explore, compare, and reconcile conflicts
                  </span>
                </div>
                <span className="text-[9px] font-mono font-bold uppercase py-1 px-2.5 bg-slate-50 border border-slate-200 rounded-lg text-slate-600">
                  KB.Batches ({pendingBatches.length})
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-[#EDD070]/30 text-[9px] font-black text-[#5A4209]/90 uppercase tracking-widest border-b border-[#C49A28]/25">
                      <th className="px-6 py-4">Batch Identity / Name</th>
                      <th className="px-6 py-4">Extracted Date</th>
                      <th className="px-6 py-4 text-right">Pending Conflicts</th>
                      <th className="px-6 py-4 text-right font-black">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#C49A28]/15 text-[#111111]">
                    {loading ? (
                      <tr>
                        <td colSpan={4} className="px-8 py-16 text-center">
                          <Loader2 className="w-5 h-5 animate-spin text-[#B88719] mx-auto" />
                        </td>
                      </tr>
                    ) : pendingBatches.map((batch: ConflictBatchGroup) => (
                      <tr
                        key={batch.batch_id}
                        onClick={() => { setSelectedBatchId(batch.batch_id); setPendingView('conflicts'); }}
                        className="group bg-[#FDF2CE]/40 hover:bg-[#F5D96A]/25 transition-all cursor-pointer"
                      >
                        <td className="px-6 py-5">
                          <div className="font-sans text-xs font-bold text-[#111111] group-hover:text-[#B88719] transition-colors flex items-center gap-2">
                            <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" />
                            <span>{batch.batch_name}</span>
                          </div>
                          <div className="text-[9px] text-[#777] font-mono mt-0.5">{batch.batch_id}</div>
                        </td>
                        <td className="px-6 py-5">
                          <div className="text-xs text-slate-600 font-mono flex items-center gap-1.5">
                            <Calendar className="w-3.5 h-3.5 text-slate-400" />
                            <span>{batch.extracted_date}</span>
                          </div>
                        </td>
                        <td className="px-6 py-5 text-right">
                          <span className={cn(
                            "px-2.5 py-1 text-[10px] font-mono font-black rounded-full border inline-block",
                            batch.number_pending_conflict > 0
                              ? "bg-amber-100 text-amber-700 border-amber-300"
                              : "bg-green-100 text-green-700 border-green-300"
                          )}>
                            {batch.number_pending_conflict} pending
                          </span>
                        </td>
                        <td className="px-6 py-5 text-right">
                          <button
                            onClick={(e) => { e.stopPropagation(); setSelectedBatchId(batch.batch_id); setPendingView('conflicts'); }}
                            className="px-3 py-1.5 bg-[#FFF9E8] border border-[#BFA66A]/45 hover:bg-[#B88719] hover:text-white rounded-lg text-[10px] font-bold text-[#8A5A00] uppercase tracking-wider transition-all inline-flex items-center gap-1 cursor-pointer"
                          >
                            <span>Explore Batch</span>
                            <ChevronRight className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(pendingPage > 1 || pendingHasNext) && (
                  <div className="flex items-center justify-between px-6 py-3 border-t border-[#BFA66A]/15 bg-[#FFFDF8]">
                    <span className="text-[10px] font-mono text-slate-400">Page {pendingPage}</span>
                    <div className="flex gap-1.5">
                      <button disabled={pendingPage === 1} onClick={() => setPendingPage(p => Math.max(1, p - 1))}
                        className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", pendingPage === 1 && "opacity-40 cursor-not-allowed")}>
                        ‹ Prev
                      </button>
                      <button disabled={!pendingHasNext} onClick={() => setPendingPage(p => p + 1)}
                        className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !pendingHasNext && "opacity-40 cursor-not-allowed")}>
                        Next ›
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* CONFLICTS TABLE */}
        {(activeTab !== 'pending' || pendingView === 'conflicts') && (
          <div className="lg:col-span-12 space-y-4">
            {activeTab === 'pending' && (
              <button
                onClick={() => { setPendingView('batches'); handleCloseDrawer(); }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#BFA66A]/35 hover:bg-[#FFF9E8]/20 rounded-xl text-xs font-bold text-[#8A5A00] transition-all cursor-pointer shadow-2xs"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                <span>Back to Ingestion Batches</span>
              </button>
            )}

            {/* BATCH RESOLUTION BAR — the whole batch is submitted together */}
            {activeTab === 'pending' && selectedBatchConflicts.length > 0 && (
              <div className="border border-[#BFA66A]/25 rounded-2xl bg-[#FFFDF8] p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-2xs">
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] font-mono">
                    Batch Resolution · {assignedMethods.length} / {selectedBatchConflicts.length} assigned
                  </span>
                  <span className={cn(
                    "text-[11px] font-medium",
                    !allConflictsAssigned ? "text-slate-500" : canResolveBatch ? "text-green-700" : "text-red-600"
                  )}>
                    {batchValidationMessage()}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={handleResolveBatch}
                  disabled={!canResolveBatch || submitting}
                  className={cn(
                    "px-5 py-2 rounded-xl text-xs font-black uppercase tracking-wider transition-all flex items-center gap-1.5 cursor-pointer shrink-0",
                    canResolveBatch && !submitting
                      ? "bg-[#B88719] text-white shadow-md hover:bg-[#8A5A00]"
                      : "bg-slate-100 text-slate-400 border border-slate-200 cursor-not-allowed"
                  )}
                >
                  {submitting ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Resolving...</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle className="w-4 h-4" />
                      <span>Resolve Batch</span>
                    </>
                  )}
                </button>
              </div>
            )}

            <div className="border border-[#BFA66A]/20 rounded-3xl overflow-hidden bg-white shadow-[0_4px_24px_rgba(184,135,25,0.02)]">
              <div className="p-4 bg-[#FFFDF8] border-b border-[#BFA66A]/15 flex items-center justify-between">
                <div>
                  <span className="text-[10px] font-mono font-black text-[#5A4209]/80 uppercase tracking-widest block">
                    {activeTab === 'pending'
                      ? `${pendingBatches.find(b => b.batch_id === selectedBatchId)?.batch_name ?? 'Batch'} Conflicts`
                      : 'Reconciliation Registry'
                    }
                  </span>
                  <span className="text-[11px] text-slate-500 font-medium">
                    {filteredSummaries.length} item{filteredSummaries.length !== 1 && 's'} listed
                  </span>
                </div>
                <span className="text-[9px] font-mono font-bold uppercase py-1 px-2 bg-slate-50 border border-slate-200 rounded-lg text-slate-600">
                  KB.Conflicts
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-[#FFFDF8] text-[9px] font-black text-[#5A4209]/80 uppercase tracking-widest border-b border-[#BFA66A]/20">
                      <th className="px-4 py-3">Conflict Type</th>
                      <th className="px-3 py-3">Severity</th>
                      <th className="px-3 py-3">Detected</th>
                      <th className="px-4 py-3 text-right font-black">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#BFA66A]/10 text-[#111111]">
                    {loading ? (
                      <tr>
                        <td colSpan={4} className="px-8 py-16 text-center">
                          <Loader2 className="w-5 h-5 animate-spin text-[#B88719] mx-auto" />
                        </td>
                      </tr>
                    ) : filteredSummaries.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="px-8 py-16 text-center text-slate-500 text-xs font-mono bg-white">
                          No conflicts registered under this criteria.
                        </td>
                      </tr>
                    ) : filteredSummaries.map((c: ConflictSummary) => {
                      const isActive = selectedConflictId === c.conflict_id;
                      return (
                        <tr
                          key={c.conflict_id}
                          onClick={() => handleOpenConflict(c)}
                          className={cn(
                            "group transition-all duration-150 cursor-pointer",
                            isActive ? "bg-[#FFF9E8] border-l-2 border-l-[#B88719]" : "hover:bg-[#FFF9E8]/20"
                          )}
                        >
                          <td className="px-4 py-4">
                            <div className="font-sans text-xs font-bold text-[#111111] group-hover:text-[#B88719] transition-colors flex items-center gap-1.5">
                              <FileText className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                              <span>{CONFLICT_TYPE_LABELS[c.conflict_type]?.title ?? c.conflict_type}</span>
                            </div>
                          </td>
                          <td className="px-3 py-4">
                            {renderSeverityBadge(c.severity)}
                          </td>
                          <td className="px-3 py-4 text-xs text-slate-500 font-mono">
                            <div className="flex items-center gap-1 text-[10px]">
                              <Clock className="w-3 h-3 text-slate-400" />
                              <span>{c.detected_at}</span>
                            </div>
                          </td>
                          <td className="px-4 py-4 text-right">
                            <div className="inline-flex items-center gap-2">
                              {activeTab === 'pending' && (
                                batchChoices[c.conflict_id] ? (
                                  <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-green-100 text-green-700 border border-green-300 inline-flex items-center gap-1">
                                    <Check className="w-2.5 h-2.5" />
                                    {RESOLUTION_LABELS[batchChoices[c.conflict_id].method]}
                                  </span>
                                ) : (
                                  <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-100 text-slate-400 border border-slate-200">
                                    Unassigned
                                  </span>
                                )
                              )}
                              <button
                                onClick={(e) => { e.stopPropagation(); handleOpenConflict(c); }}
                                className={cn(
                                  "px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all inline-flex items-center gap-0.5 cursor-pointer",
                                  isActive
                                    ? "bg-[#B88719] text-white shadow-xs"
                                    : "bg-[#FFF9E8] border border-[#BFA66A]/45 hover:bg-[#B88719] hover:text-white text-[#8A5A00]"
                                )}
                              >
                                <span>{isActive ? 'Active' : 'Review'}</span>
                                <ChevronRight className="w-2.5 h-2.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {activeTab !== 'pending' && (
                  (activeTab === 'awaiting' ? (awaitingPage > 1 || awaitingHasNext) : (resolvedPage > 1 || resolvedHasNext))
                ) && (
                  <div className="flex items-center justify-between px-6 py-3 border-t border-[#BFA66A]/15 bg-[#FFFDF8]">
                    <span className="text-[10px] font-mono text-slate-400">
                      Page {activeTab === 'awaiting' ? awaitingPage : resolvedPage}
                    </span>
                    <div className="flex gap-1.5">
                      {activeTab === 'awaiting' ? (
                        <>
                          <button disabled={awaitingPage === 1} onClick={() => setAwaitingPage(p => Math.max(1, p - 1))}
                            className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", awaitingPage === 1 && "opacity-40 cursor-not-allowed")}>
                            ‹ Prev
                          </button>
                          <button disabled={!awaitingHasNext} onClick={() => setAwaitingPage(p => p + 1)}
                            className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !awaitingHasNext && "opacity-40 cursor-not-allowed")}>
                            Next ›
                          </button>
                        </>
                      ) : (
                        <>
                          <button disabled={resolvedPage === 1} onClick={() => setResolvedPage(p => Math.max(1, p - 1))}
                            className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", resolvedPage === 1 && "opacity-40 cursor-not-allowed")}>
                            ‹ Prev
                          </button>
                          <button disabled={!resolvedHasNext} onClick={() => setResolvedPage(p => p + 1)}
                            className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !resolvedHasNext && "opacity-40 cursor-not-allowed")}>
                            Next ›
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

      </div>

    </div>
  );
};
