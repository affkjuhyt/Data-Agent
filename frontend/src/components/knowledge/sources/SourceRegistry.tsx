import React, { useEffect, useState } from 'react';
import { Globe, Trash2, X, Loader2, Clock, ArrowRight, Layers, CheckCircle2, AlertCircle, Search, Pencil, Save } from 'lucide-react';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { FilterPopover } from '../../shared/FilterPopover';
import { cn } from '../../../lib/utils';
import {
  ConnectorType, CONNECTOR_LABEL, ScheduleState,
  buildCron, cronToHuman, cronToScheduleState, isCronRepresentable,
  parseQueryParams, queryParamsToText,
} from './sourceShared';
import { SchedulePicker } from './SchedulePicker';
import { RestSdmxFields, RecursiveCrawlFields, PromotionModeControl } from './SourceConfigFields';

interface SourceRow {
  id: string;
  title: string;
  publisher: string | null;
  connector_type: ConnectorType;
  url: string | null;
  cron: string | null;
  render: boolean;
  openbao_path: string | null;
  query_params: Record<string, string> | null;
  auth_style: string | null;
  auth_param_name: string | null;
  recursive: boolean;
  max_depth: number;
  same_domain_only: boolean;
  promotion_mode: 'manual' | 'auto';
  created_by: string | null;
  created_at: string;
  last_run_status: 'queued' | 'running' | 'success' | 'failed' | null;
  last_run_at: string | null;
  next_run_at: string | null;
  // The crawl above can succeed while the resulting document's own Silver/Gold
  // attempt then fails outright (bad file format, corrupt content) — these
  // reflect that real downstream outcome, separate from the crawl's own status.
  last_doc_id: string | null;
  last_doc_current_tier: string | null;
  last_doc_pipeline_stage: string | null;
  last_doc_pipeline_status: string | null;
  last_doc_pipeline_error: string | null;
}

interface SourceRun {
  run_id: string;
  status: string;
  trigger: string;
  retry_count: number;
  started_at: string;
  finished_at: string | null;
  data_ids: string[];
  error: string | null;
  quality_metrics: Record<string, unknown> | null;
}

const MAX_RETRIES = 5; // mirrors platform_workers/workers/web_crawler/worker.py's MAX_RETRIES

const STATUS_TONE: Record<string, string> = {
  success: 'text-green-700 bg-green-50 border-green-200',
  failed: 'text-red-700 bg-red-50 border-red-200',
  running: 'text-amber-700 bg-amber-50 border-amber-200',
  queued: 'text-slate-700 bg-slate-50 border-slate-200',
};

type StatusFilter = 'active' | 'failed' | 'never' | 'processing_failed';
const STATUS_FILTER_LABEL: Record<StatusFilter, string> = {
  active: 'Queued / Running', failed: 'Failed', never: 'Never run',
  processing_failed: 'Auto-processing failed',
};

/** "in 6 min" / "in 3 h" / "in 2 d"; "due now" once the time has passed. */
function relativeFromNow(iso: string): string {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (minutes <= 0) return 'due now';
  if (minutes < 60) return `in ${minutes} min`;
  if (minutes < 60 * 48) return `in ${Math.round(minutes / 60)} h`;
  return `in ${Math.round(minutes / 1440)} d`;
}

interface EditFormState {
  url: string; schedule: ScheduleState; originalCron: string; render: boolean;
  openbaoPath: string; queryParamsText: string; authStyle: string; authParamName: string;
  recursive: boolean; maxDepth: number; sameDomainOnly: boolean; promotionMode: 'manual' | 'auto';
}

function editFormFromSource(s: SourceRow): EditFormState {
  return {
    url: s.url || '', schedule: cronToScheduleState(s.cron), originalCron: s.cron || '', render: s.render,
    openbaoPath: s.openbao_path || '', queryParamsText: queryParamsToText(s.query_params),
    authStyle: s.auth_style || 'bearer', authParamName: s.auth_param_name || '',
    recursive: s.recursive, maxDepth: s.max_depth, sameDomainOnly: s.same_domain_only,
    promotionMode: s.promotion_mode || 'manual',
  };
}

interface SourceRegistryProps {
  /** Sources with a run queued or running right now. Owned by the Knowledge Ops
   *  shell (like `promotingIds` for Data Layers) so the lock survives switching tabs. */
  activeSourceIds?: Set<string>;
  onRunQueued?: (runIds: string[]) => void;
}

export const SourceRegistry = ({ activeSourceIds = new Set(), onRunQueued }: SourceRegistryProps) => {
  const { items: sources, page, totalPages, hasNext, isLoading, canJump, goTo, reload } =
    useCursorPagination<SourceRow>('/api/knowledge/sources', {}, []);

  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [showVerification, setShowVerification] = useState(false);
  const [isBatchRunning, setIsBatchRunning] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);
  // Rows deleted this session leave the table at once; the silent refresh then
  // confirms it (same feel as Data Layers, no full-list reload).
  const [removedIds, setRemovedIds] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilters, setStatusFilters] = useState<Set<StatusFilter>>(new Set());

  const [selected, setSelected] = useState<SourceRow | null>(null);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [drawerTab, setDrawerTab] = useState<'runs' | 'edit'>('runs');
  const [editForm, setEditForm] = useState<EditFormState | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const isActive = (s: SourceRow) =>
    activeSourceIds.has(s.id) || s.last_run_status === 'queued' || s.last_run_status === 'running';
  const hasActive = sources.some(isActive);
  // While anything is queued or running, refresh the list so Last Run and the
  // lock follow the real state; the state itself comes from the database.
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => reload(true), 3000);
    return () => clearInterval(timer);
  }, [hasActive, reload]);

  const query = searchQuery.trim().toLowerCase();
  const matchesFilters = (s: SourceRow) => {
    if (query && !`${s.title} ${s.url ?? ''} ${s.publisher ?? ''}`.toLowerCase().includes(query)) return false;
    if (statusFilters.size === 0) return true;
    return (statusFilters.has('active') && isActive(s))
      || (statusFilters.has('failed') && s.last_run_status === 'failed')
      || (statusFilters.has('never') && !s.last_run_status)
      || (statusFilters.has('processing_failed') && s.last_doc_pipeline_status === 'failed');
  };
  const liveSources = sources.filter(s => !removedIds.has(s.id));
  const visible = liveSources.filter(matchesFilters);
  const nextRunTs = (s: SourceRow) => (s.next_run_at ? Date.parse(s.next_run_at) : Number.POSITIVE_INFINITY);
  // Manual-only sources first, then the scheduled ones with the soonest run on top.
  const groups = [
    { key: 'manual', label: 'No schedule — manual only', rows: visible.filter(s => !s.cron?.trim()) },
    {
      key: 'scheduled', label: 'Scheduled — next run soonest first',
      rows: visible.filter(s => s.cron?.trim()).sort((a, b) => nextRunTs(a) - nextRunTs(b)),
    },
  ].filter(g => g.rows.length > 0);

  const selectedSources = liveSources.filter(s => selectedIds.includes(s.id));
  const visibleIds = visible.map(s => s.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selectedIds.includes(id));
  const toggleSelect = (id: string) =>
    setSelectedIds(prev => (prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]));
  const toggleSelectAll = () =>
    setSelectedIds(prev => (allVisibleSelected
      ? prev.filter(id => !visibleIds.includes(id))
      : Array.from(new Set([...prev, ...visibleIds]))));
  const toggleStatusFilter = (id: string) =>
    setStatusFilters(prev => {
      const next = new Set(prev);
      if (next.has(id as StatusFilter)) next.delete(id as StatusFilter); else next.add(id as StatusFilter);
      return next;
    });

  const openRuns = (source: SourceRow) => {
    setSelected(source);
    setDrawerTab('runs');
    setEditForm(editFormFromSource(source));
    setEditError(null);
    setRunsLoading(true);
    apiGet<SourceRun[]>(`/api/knowledge/sources/${source.id}/runs`)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setRunsLoading(false));
  };

  // A source with a cron runs indefinitely — the upstream URL, an auth
  // param, or the schedule itself may need to change without recreating the
  // whole source (and losing its run history). PATCH keeps the same
  // versioned-config shape update_source_config already implements.
  const submitEdit = async () => {
    if (!selected || !editForm) return;
    if (!editForm.url.trim()) { setEditError('URL is required.'); return; }
    setEditSaving(true);
    setEditError(null);
    try {
      const es = editForm.schedule;
      const cron = buildCron(es.mode, es.time, es.days, es.monthDays, es.dayOfMonth, es.months, es.hourEvery, es.minuteEvery);
      const updated = await apiMutate<SourceRow>('PATCH', `/api/knowledge/sources/${selected.id}`, {
        url: editForm.url.trim(), cron, render: editForm.render,
        openbao_path: editForm.openbaoPath || undefined,
        query_params: parseQueryParams(editForm.queryParamsText),
        auth_style: editForm.openbaoPath ? editForm.authStyle : undefined,
        auth_param_name: editForm.authStyle === 'query_param' ? editForm.authParamName || undefined : undefined,
        recursive: editForm.recursive, max_depth: editForm.maxDepth, same_domain_only: editForm.sameDomainOnly,
        promotion_mode: editForm.promotionMode,
      });
      setSelected(prev => (prev ? { ...prev, ...updated } : prev));
      setDrawerTab('runs');
      reload(true);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to save changes.');
    } finally {
      setEditSaving(false);
    }
  };

  const dropRemoved = (ids: string[]) =>
    setRemovedIds(prev => { const next = new Set(prev); ids.forEach(id => next.delete(id)); return next; });

  const deleteSource = async (id: string) => {
    setPendingDeleteId(null);
    setSelectedIds(prev => prev.filter(item => item !== id)); // a deleted row must not stay counted as selected
    setRemovedIds(prev => new Set(prev).add(id));
    try {
      await apiMutate('DELETE', `/api/knowledge/sources/${id}`);
    } catch {
      dropRemoved([id]); // the delete failed: the row comes back
    }
    reload(true);
  };

  const handleBatchDelete = async () => {
    setIsBatchDeleting(true);
    const deleted: string[] = [];
    for (const source of selectedSources) {
      if (isActive(source)) continue; // a queued or running source is left alone
      try {
        await apiMutate('DELETE', `/api/knowledge/sources/${source.id}`);
        deleted.push(source.id);
      } catch { /* it stays in the list */ }
    }
    setRemovedIds(prev => new Set([...prev, ...deleted]));
    setSelectedIds(prev => prev.filter(id => !deleted.includes(id)));
    setIsBatchDeleting(false);
    setShowDeleteConfirm(false);
    reload(true);
  };

  // 409 = that source is already queued or running: nothing to add.
  const queueRun = async (id: string): Promise<string | null> => {
    try {
      const queued = await apiMutate<{ run_id: string }>('POST', `/api/knowledge/sources/${id}/run-now`);
      return queued.run_id;
    } catch {
      return null;
    }
  };

  const runNow = async (id: string) => {
    const runId = await queueRun(id);
    onRunQueued?.(runId ? [runId] : []);
    reload(true);
  };

  // The crawl already succeeded and landed the document — a failed Silver/Gold
  // attempt just needs that one stage re-queued, not a whole new crawl (which,
  // for unchanged content, would be a no-op anyway per the content-hash skip).
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set());
  const retryProcessing = async (source: SourceRow) => {
    if (!source.last_doc_id) return;
    const targetLayer = source.last_doc_current_tier === 'silver' ? 'gold' : 'silver';
    setRetryingIds(prev => new Set(prev).add(source.id));
    try {
      await apiMutate('POST', `/api/pipeline/ingestion/promote/${source.last_doc_id}/${targetLayer}`);
    } catch { /* surfaced naturally on the next reload's still-failed status */ }
    setRetryingIds(prev => { const next = new Set(prev); next.delete(source.id); return next; });
    reload(true);
  };

  const handleBatchRun = async () => {
    setIsBatchRunning(true);
    const runIds: string[] = [];
    for (const source of selectedSources) {
      if (isActive(source) || source.connector_type === 'upload') continue; // nothing to re-fetch
      const runId = await queueRun(source.id);
      if (runId) runIds.push(runId);
    }
    setIsBatchRunning(false);
    setSelectedIds([]);
    setShowVerification(false);
    onRunQueued?.(runIds);
    reload(true);
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Globe className="w-4 h-4 text-[#B88719]" />
          <h3 className="text-sm font-bold text-[#1E1B15]">Source Registry</h3>
          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]">{liveSources.length}</span>
        </div>
        <span className="text-[10px] font-mono text-[#8A8A8A]">Use "Add Data" to register a new source, single or bulk.</span>
      </div>

      {/* ─── Filters + search, same row as Data Layers ─── */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <FilterPopover
          activeCount={statusFilters.size}
          groups={[{
            label: 'Run status',
            options: (Object.keys(STATUS_FILTER_LABEL) as StatusFilter[]).map(id => ({ id, label: STATUS_FILTER_LABEL[id] })),
            isActive: (id) => statusFilters.has(id as StatusFilter),
            onToggle: toggleStatusFilter,
          }]}
        />
        <div className="relative group w-full sm:w-80 sm:ml-auto">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A5A00] opacity-65 group-focus-within:opacity-100 transition-opacity" />
          <input
            type="text"
            placeholder="Search by title, URL or publisher..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="ui-input pl-12 pr-6 py-2.5 text-xs w-full font-mono"
          />
        </div>
      </div>

      {/* ─── Batch action bar, same as Data Layers ─── */}
      <div className="content-card content-card--static flex flex-col sm:flex-row justify-between items-start sm:items-center p-4 gap-3 shadow-2xs">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-bold text-[#111111]">Batch Operations Configurator</span>
          <span className="text-[10px] font-mono text-[#5F5F5F]">
            Select multiple sources using checkboxes to queue several crawls at once.
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => { if (selectedSources.length > 0) setShowDeleteConfirm(true); }}
          disabled={selectedSources.length === 0}
          className={cn(
            'px-5 py-2.5 rounded-xl text-[11px] font-black uppercase tracking-wider transition-all flex items-center gap-2 cursor-pointer',
            selectedSources.length > 0
              ? 'bg-red-50 text-red-600 border border-red-300 hover:bg-red-100 shadow-sm'
              : 'bg-[#FFF9E8] border border-[#BFA66A] text-[#5F5F5F] cursor-not-allowed',
          )}
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Delete Selected</span>
          <span className="bg-red-200 text-red-800 font-mono text-[9px] w-4.5 h-4.5 flex items-center justify-center rounded-full font-black">
            {selectedSources.length}
          </span>
        </button>
        <button
          onClick={() => { if (selectedSources.length > 0) setShowVerification(true); }}
          disabled={selectedSources.length === 0}
          className={cn(
            'px-5 py-2.5 rounded-xl text-[11px] font-black uppercase tracking-wider transition-all flex items-center gap-2 cursor-pointer',
            selectedSources.length > 0
              ? 'bg-[#1E1B15] text-[#FAFAFA] border border-[#BFA66A] hover:bg-[#2C271E] shadow-sm transform hover:-translate-y-0.5 active:translate-y-0'
              : 'bg-[#FFF9E8] border border-[#BFA66A] text-[#5F5F5F] cursor-not-allowed',
          )}
        >
          <Layers className="w-3.5 h-3.5 text-[#D9B86C]" />
          <span>Multiple Run</span>
          <span className="bg-[#BFA66A] text-[#111111] font-mono text-[9px] w-4.5 h-4.5 flex items-center justify-center rounded-full font-black">
            {selectedSources.length}
          </span>
        </button>
        </div>
      </div>

      <div className="content-card content-card--static overflow-hidden">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-[#FFFDF8] text-[10px] font-black text-[#5A4209] uppercase tracking-widest border-b border-[#BFA66A]">
              <th className="px-6 py-5 w-12 text-center">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAll}
                  className="w-4 h-4 rounded border-[#BFA66A] text-[#B88719] focus:ring-[#B88719] cursor-pointer"
                />
              </th>
              <th className="px-6 py-5">Source</th>
              <th className="px-6 py-5">Connector</th>
              <th className="px-6 py-5">Promotion</th>
              <th className="px-6 py-5">Schedule</th>
              <th className="px-6 py-5">Next Run</th>
              <th className="px-6 py-5">Last Run</th>
              <th className="px-8 py-5 text-right font-black">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A] text-[#111111]">
            {isLoading ? (
              <tr><td colSpan={8} className="px-8 py-12 text-center bg-white">
                <div className="flex items-center justify-center gap-2 text-[#5F5F5F] text-xs font-mono">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              </td></tr>
            ) : visible.length === 0 ? (
              <tr><td colSpan={8} className="px-8 py-12 text-center text-[#5F5F5F] text-xs font-mono bg-white">
                {liveSources.length === 0
                  ? 'No sources registered. Use "Add Data" to register one, single or bulk.'
                  : 'No sources match the current filters.'}
              </td></tr>
            ) : groups.map(group => (
              <React.Fragment key={group.key}>
                <tr>
                  <td colSpan={8} className="px-6 py-2.5 bg-[#FFF9E8] text-[10px] font-black uppercase tracking-widest text-[#8A5A00]">
                    {group.label} <span className="font-mono text-[#5F5F5F]">({group.rows.length})</span>
                  </td>
                </tr>
                {group.rows.map(source => {
                  const active = isActive(source);
                  return (
                    <tr
                      key={source.id}
                      className={cn(
                        'group transition-colors cursor-pointer',
                        active
                          ? 'bg-amber-50/60 animate-pulse'
                          : selectedIds.includes(source.id)
                            ? 'bg-[#FDE8A0]/40'
                            : 'bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/60',
                      )}
                      onClick={() => openRuns(source)}
                    >
                      <td className="px-6 py-6 w-12 text-center" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(source.id)}
                          onChange={() => toggleSelect(source.id)}
                          className="w-4 h-4 rounded border-[#BFA66A] text-[#B88719] focus:ring-[#B88719] cursor-pointer"
                        />
                      </td>
                      <td className="px-6 py-6">
                        <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors">{source.title}</div>
                        <div className="text-[10px] font-mono text-[#8A5A00] truncate max-w-xs">{source.url}</div>
                      </td>
                      <td className="px-6 py-6 font-mono text-xs text-[#3F3F3F]">{CONNECTOR_LABEL[source.connector_type]}</td>
                      <td className="px-6 py-6">
                        <span
                          className={cn(
                            'px-2 py-0.5 rounded border text-[10px] font-bold uppercase',
                            source.promotion_mode === 'auto'
                              ? 'text-[#8A5A00] bg-[#FFF3D6] border-[#D9B86C]'
                              : 'text-slate-700 bg-slate-50 border-slate-200',
                          )}
                          title={source.promotion_mode === 'auto' ? 'Runs straight through to Gold, no click needed' : 'Needs a manual Process click at each tier'}
                        >
                          {source.promotion_mode === 'auto' ? 'Auto' : 'Manual'}
                        </span>
                      </td>
                      <td className="px-6 py-6 font-mono text-xs text-[#5F5F5F]">
                        <span className="inline-flex items-center gap-1" title={source.cron || undefined}><Clock className="w-3 h-3" />{cronToHuman(source.cron)}</span>
                      </td>
                      <td className="px-6 py-6 font-mono text-xs text-[#5F5F5F]" title={source.next_run_at || undefined}>
                        {source.next_run_at
                          ? relativeFromNow(source.next_run_at)
                          : (source.cron?.trim() && !source.last_run_status ? 'Not started' : '—')}
                      </td>
                      <td className="px-6 py-6 text-xs">
                        {source.last_run_status ? (
                          <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${STATUS_TONE[source.last_run_status] || ''}`}>
                            {source.last_run_status}
                          </span>
                        ) : <span className="text-[#8A8A8A]">never run</span>}
                        {source.last_doc_pipeline_status === 'failed' && (
                          // The crawl above succeeded — this is the document's OWN
                          // Silver/Gold attempt failing outright afterward (bad file
                          // format, corrupt content), which a plain crawl "success"
                          // badge would otherwise hide completely.
                          <div className="mt-1">
                            <span
                              className="px-2 py-0.5 rounded border text-[10px] font-bold uppercase text-red-700 bg-red-50 border-red-200"
                              title={source.last_doc_pipeline_error || undefined}
                            >
                              {source.last_doc_pipeline_stage || 'silver'} failed
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="px-8 py-6 text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-2.5">
                          {active ? (
                            <span className="text-[10px] font-mono font-bold text-[#B88719] uppercase tracking-wide flex items-center justify-end gap-1.5">
                              <span className="w-3 h-3 rounded-full border-2 border-[#B88719] border-t-[#B88719] animate-spin" />
                              {source.last_run_status === 'queued' ? 'Queued...' : 'Running...'}
                            </span>
                          ) : source.connector_type === 'upload' ? (
                            <span className="text-[10px] font-mono text-[#8A8A8A]" title="Re-upload the same title to add a new run">no fetch to re-run</span>
                          ) : (
                            <button onClick={() => runNow(source.id)} className="btn-ghost btn-sm" title="Run now">
                              Run Now
                              <ArrowRight className="w-3 h-3" />
                            </button>
                          )}
                          {!active && source.last_doc_pipeline_status === 'failed' && (
                            retryingIds.has(source.id) ? (
                              <span className="text-[10px] font-mono font-bold text-[#B88719] uppercase tracking-wide flex items-center gap-1.5">
                                <Loader2 className="w-3 h-3 animate-spin" /> Retrying...
                              </span>
                            ) : (
                              <button
                                onClick={() => retryProcessing(source)}
                                className="btn-ghost btn-sm text-red-700"
                                title={source.last_doc_pipeline_error || 'Re-queue Silver/Gold for this document'}
                              >
                                Retry Processing
                              </button>
                            )
                          )}
                          {!active && (pendingDeleteId === source.id ? (
                            <span className="inline-flex items-center gap-1.5">
                              <button onClick={() => deleteSource(source.id)} className="px-2.5 py-1.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all cursor-pointer">Confirm</button>
                        <button onClick={() => setPendingDeleteId(null)} className="p-1.5 bg-white border border-[#BFA66A] hover:bg-[#FFF9E8] text-[#5F5F5F] rounded-lg transition-colors cursor-pointer"><X className="w-3 h-3" /></button>
                            </span>
                          ) : (
                            <button
                        onClick={() => setPendingDeleteId(source.id)}
                        className="p-1.5 opacity-0 group-hover:opacity-100 bg-red-50 hover:bg-red-100 border border-red-200 text-red-500 rounded-lg transition-all cursor-pointer"
                        title="Delete source"
                      ><Trash2 className="w-3.5 h-3.5" /></button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {(totalPages > 1 || hasNext) && (
        <div className="flex justify-center pt-2">
          <Pagination page={page} totalPages={totalPages} hasNext={hasNext} canJump={canJump} onJump={goTo} />
        </div>
      )}

      {/* ── Source detail drawer: Run history / Edit ── */}
      <DetailDrawer
        isOpen={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.title || 'Source'}
        subtitle={CONNECTOR_LABEL[selected?.connector_type || 'web_crawl']}
        icon={Globe}
        tabs={[{ id: 'runs', label: 'Run history', icon: Clock }, { id: 'edit', label: 'Edit', icon: Pencil }]}
        activeTab={drawerTab}
        onTabChange={(id) => setDrawerTab(id as 'runs' | 'edit')}
      >
        {drawerTab === 'runs' ? (
          runsLoading ? (
            <div className="flex items-center gap-2 text-xs font-mono text-[#5F5F5F] p-6"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>
          ) : runs.length === 0 ? (
            <div className="p-6 text-xs font-mono text-[#5F5F5F]">No runs yet — use "Run Now" or wait for the next scheduled tick.</div>
          ) : (
            <div className="divide-y divide-[#EDE6D3]">
              {runs.map(run => (
                <div key={run.run_id} className="p-4 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${STATUS_TONE[run.status] || ''}`}>{run.status}</span>
                    <span className="text-[10px] font-mono text-[#8A8A8A]">{run.trigger} · {run.started_at.slice(0, 19).replace('T', ' ')}</span>
                  </div>
                  {run.error && <div className="text-xs font-mono text-red-600">{run.error}</div>}
                  {run.quality_metrics?.unchanged === true && (
                    <div className="text-xs font-mono text-[#5F5F5F]">
                      No change since the last run — kept the existing document
                      {run.quality_metrics.existing_tier ? ` (currently ${String(run.quality_metrics.existing_tier).toUpperCase()})` : ''}.
                      Nothing new was added to Bronze.
                    </div>
                  )}
                  {run.status === 'failed' && (
                    <div className="text-[10px] font-mono text-amber-700">
                      {run.retry_count >= MAX_RETRIES ? 'max retries reached' : `retry ${run.retry_count}/${MAX_RETRIES}`}
                    </div>
                  )}
                  {run.quality_metrics && (
                    <div className="text-[10px] font-mono text-[#5F5F5F]">
                      {Object.entries(run.quality_metrics).filter(([, v]) => v !== null && v !== undefined)
                        .map(([k, v]) => `${k}=${v}`).join(' · ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )
        ) : selected && editForm && (
          <div className="p-4 space-y-4">
            {selected.connector_type === 'upload' ? (
              <div className="text-xs font-mono text-[#5F5F5F]">
                This entry records an uploaded file — there is no URL or schedule to edit. Re-upload the same title to add a new run.
              </div>
            ) : (
              <>
                {editError && <div className="text-xs text-red-600 font-mono">{editError}</div>}
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">URL</label>
                  <input
                    className="ui-input w-full text-xs px-3 py-2 font-mono"
                    value={editForm.url}
                    onChange={e => setEditForm(f => f && { ...f, url: e.target.value })}
                    placeholder="https://..."
                  />
                  <div className="text-[10px] font-mono text-[#8A8A8A] mt-1">
                    Update this if the publisher moved the file/page/endpoint — the source keeps its title and run history.
                  </div>
                </div>

                {(selected.connector_type === 'rest_api' || selected.connector_type === 'sdmx') && (
                  <RestSdmxFields
                    value={editForm}
                    onChange={next => setEditForm(f => f && { ...f, ...next })}
                  />
                )}

                {(selected.connector_type === 'web_crawl' || selected.connector_type === 'web_crawl_headless') && (
                  <RecursiveCrawlFields value={editForm} onChange={next => setEditForm(f => f && { ...f, ...next })} />
                )}

                <SchedulePicker
                  {...editForm.schedule}
                  onChange={s => setEditForm(f => f && { ...f, schedule: s })}
                  footnote="Changing the schedule takes effect from the next run — it does not trigger an immediate re-run."
                  unrepresentableCron={!isCronRepresentable(editForm.originalCron) ? editForm.originalCron : null}
                />

                <PromotionModeControl
                  value={editForm.promotionMode}
                  effective={editForm.promotionMode}
                  onChange={mode => setEditForm(f => f && { ...f, promotionMode: mode })}
                />

                <div className="flex justify-end gap-2 pt-2">
                  <button className="btn-ghost btn-sm" onClick={() => { setEditForm(editFormFromSource(selected)); setEditError(null); }}>Reset</button>
                  <button className="btn-gold !text-xs" onClick={submitEdit} disabled={editSaving}>
                    {editSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </DetailDrawer>

      {/* ── Bulk import ── */}
      {/* ── Batch Delete Verification (same modal shell as the run verification) ── */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs select-none">
          <div className="bg-[#13110E] border border-[#BFA66A] rounded-[2rem] w-full max-w-lg shadow-2xl flex flex-col overflow-hidden text-white pointer-events-auto">
            <div className="p-6 border-b border-[#BFA66A] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Trash2 className="w-5 h-5 text-red-400" />
                <div>
                  <h3 className="text-sm font-display font-black uppercase tracking-wider text-[#D9B86C]">Batch Delete Verification</h3>
                  <p className="text-[10px] font-mono text-[#5F5F5F] mt-0.5">Please confirm sources to remove from the registry</p>
                </div>
              </div>
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="p-1.5 hover:bg-white/10 rounded-xl text-slate-400 hover:text-white transition-all cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 flex-1 overflow-y-auto max-h-[320px] space-y-3 custom-scrollbar">
              {selectedSources.length === 0 ? (
                <div className="py-12 text-center text-[#5F5F5F] text-xs font-mono flex flex-col items-center gap-2">
                  <AlertCircle className="w-6 h-6 text-amber-500" />
                  <span>No sources selected.</span>
                </div>
              ) : (
                selectedSources.map(source => (
                  <div
                    key={source.id}
                    className="flex items-center justify-between p-3.5 bg-white/[0.03] rounded-xl border border-white/15 hover:border-[#BFA66A] transition-all gap-4"
                  >
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => toggleSelect(source.id)}
                        aria-label="Remove from selection"
                        className="p-1.5 bg-white/5 hover:bg-white/10 text-slate-300 rounded-lg transition-colors border border-white/15 cursor-pointer flex items-center justify-center"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                      <div className="min-w-0">
                        <h4 className="text-xs font-bold text-slate-100 uppercase tracking-tight truncate max-w-[240px]">{source.title}</h4>
                        <span className="text-[9px] font-mono text-[#5F5F5F] uppercase tracking-widest mt-0.5 block">
                          {isActive(source) ? 'Queued or running — will be skipped' : `ID: ${source.id}`}
                        </span>
                      </div>
                    </div>
                    <span className="shrink-0 bg-white/5 border border-white/20 text-slate-300 text-[10px] font-mono px-2.5 py-1 rounded-lg">
                      {CONNECTOR_LABEL[source.connector_type]}
                    </span>
                  </div>
                ))
              )}
              <p className="text-[10px] font-mono text-[#5F5F5F] pt-1">
                The sources leave the registry. Documents they already added to Bronze stay in Data Layers.
              </p>
            </div>

            <div className="p-6 bg-white/[0.01] border-t border-[#BFA66A] flex items-center justify-between gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 bg-transparent hover:bg-white/5 text-slate-300 hover:text-white rounded-xl text-[10px] font-bold uppercase tracking-wider transition-all border border-white/20 cursor-pointer"
              >
                Discard
              </button>
              <button
                onClick={handleBatchDelete}
                disabled={selectedSources.filter(s => !isActive(s)).length === 0 || isBatchDeleting}
                className={cn(
                  'px-6 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all inline-flex items-center gap-2 cursor-pointer',
                  selectedSources.filter(s => !isActive(s)).length > 0 && !isBatchDeleting
                    ? 'bg-red-500 text-white hover:bg-red-600 shadow-md'
                    : 'bg-slate-700 text-slate-400 cursor-not-allowed',
                )}
              >
                {isBatchDeleting ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-red-200 border-t-white animate-spin" />
                    <span>Deleting...</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Confirm Batch Delete</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Batch Run Verification (same modal as Data Layers' Batch Pipeline Verification) ── */}
      {showVerification && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs select-none">
          <div className="bg-[#13110E] border border-[#BFA66A] rounded-[2rem] w-full max-w-lg shadow-2xl flex flex-col overflow-hidden text-white pointer-events-auto">
            <div className="p-6 border-b border-[#BFA66A] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Layers className="w-5 h-5 text-[#D9B86C]" />
                <div>
                  <h3 className="text-sm font-display font-black uppercase tracking-wider text-[#D9B86C]">Batch Run Verification</h3>
                  <p className="text-[10px] font-mono text-[#5F5F5F] mt-0.5">Please confirm sources staged for a crawl into BRONZE</p>
                </div>
              </div>
              <button
                onClick={() => setShowVerification(false)}
                className="p-1.5 hover:bg-white/10 rounded-xl text-slate-400 hover:text-white transition-all cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 flex-1 overflow-y-auto max-h-[320px] space-y-3 custom-scrollbar">
              {selectedSources.length === 0 ? (
                <div className="py-12 text-center text-[#5F5F5F] text-xs font-mono flex flex-col items-center gap-2">
                  <AlertCircle className="w-6 h-6 text-amber-500" />
                  <span>No sources selected. Please add items back or close table select view.</span>
                </div>
              ) : (
                selectedSources.map(source => (
                  <div
                    key={source.id}
                    className="flex items-center justify-between p-3.5 bg-white/[0.03] rounded-xl border border-white/15 hover:border-[#BFA66A] transition-all gap-4"
                  >
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => toggleSelect(source.id)}
                        aria-label="Remove from selection"
                        className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-500 hover:text-red-400 rounded-lg transition-colors border border-red-500/15 cursor-pointer flex items-center justify-center"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                      <div className="min-w-0">
                        <h4 className="text-xs font-bold text-slate-100 uppercase tracking-tight truncate max-w-[240px]">{source.title}</h4>
                        <span className="text-[9px] font-mono text-[#5F5F5F] uppercase tracking-widest mt-0.5 block">
                          {isActive(source)
                            ? 'Already queued or running — will be skipped'
                            : source.connector_type === 'upload'
                              ? 'Uploaded file — no fetch to re-run, will be skipped'
                              : `ID: ${source.id}`}
                        </span>
                      </div>
                    </div>
                    <div className="shrink-0 flex items-center gap-2.5">
                      <span className="bg-white/5 border border-white/20 text-slate-300 text-[10px] font-mono px-2.5 py-1 rounded-lg">
                        {CONNECTOR_LABEL[source.connector_type]}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="p-6 bg-white/[0.01] border-t border-[#BFA66A] flex items-center justify-between gap-3">
              <button
                onClick={() => setShowVerification(false)}
                className="px-4 py-2 bg-transparent hover:bg-white/5 text-slate-300 hover:text-white rounded-xl text-[10px] font-bold uppercase tracking-wider transition-all border border-white/20 cursor-pointer"
              >
                Discard
              </button>
              <button
                onClick={handleBatchRun}
                disabled={selectedSources.length === 0 || isBatchRunning}
                className={cn(
                  'px-6 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all inline-flex items-center gap-2 cursor-pointer',
                  selectedSources.length > 0 && !isBatchRunning
                    ? 'bg-[#D9B86C] text-[#13110E] hover:bg-[#FFE099] shadow-md'
                    : 'bg-slate-700 text-slate-400 cursor-not-allowed',
                )}
              >
                {isBatchRunning ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-[#8A5A00] border-t-white animate-spin" />
                    <span>Queueing Sequence...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Confirm Batch Run</span>
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
