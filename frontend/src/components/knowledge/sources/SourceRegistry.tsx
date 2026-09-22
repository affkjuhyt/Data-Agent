import React, { useEffect, useState } from 'react';
import { Globe, Plus, Trash2, X, Loader2, Upload, Clock, ArrowRight, Layers, CheckCircle2, AlertCircle, Search } from 'lucide-react';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { FilterPopover } from '../../shared/FilterPopover';
import { cn } from '../../../lib/utils';

type ConnectorType = 'http_file' | 'web_crawl' | 'web_crawl_headless' | 'rss' | 'rest_api' | 'sdmx';

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
  created_by: string | null;
  created_at: string;
  last_run_status: 'queued' | 'running' | 'success' | 'failed' | null;
  last_run_at: string | null;
  next_run_at: string | null;
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

const CONNECTOR_LABEL: Record<ConnectorType, string> = {
  http_file: 'HTTP File',
  web_crawl: 'Web Crawl',
  web_crawl_headless: 'Web Crawl (headless)',
  rss: 'RSS Feed',
  rest_api: 'REST API',
  sdmx: 'SDMX',
};

// Short enough to sit on a pill (full description goes in the `title` tooltip).
const CONNECTOR_PILL_LABEL: Record<ConnectorType, string> = {
  http_file: 'Remote File',
  web_crawl: 'Web Page',
  web_crawl_headless: 'Web Page (JS)',
  rss: 'RSS Feed',
  rest_api: 'REST API',
  sdmx: 'SDMX',
};

const CONNECTOR_PILL_DESCRIPTION: Record<ConnectorType, string> = {
  http_file: 'PDF/DOCX/XLSX at a direct URL',
  web_crawl: 'Static HTML',
  web_crawl_headless: 'JS-rendered / headless',
  rss: 'RSS feed',
  rest_api: 'REST API with query params + auth',
  sdmx: 'SDMX statistical API',
};

const CONNECTOR_TYPES: ConnectorType[] = ['web_crawl', 'web_crawl_headless', 'rss', 'rest_api', 'sdmx', 'http_file'];

const STATUS_TONE: Record<string, string> = {
  success: 'text-green-700 bg-green-50 border-green-200',
  failed: 'text-red-700 bg-red-50 border-red-200',
  running: 'text-amber-700 bg-amber-50 border-amber-200',
  queued: 'text-slate-700 bg-slate-50 border-slate-200',
};

type StatusFilter = 'active' | 'failed' | 'never';
const STATUS_FILTER_LABEL: Record<StatusFilter, string> = {
  active: 'Queued / Running', failed: 'Failed', never: 'Never run',
};

/** "in 6 min" / "in 3 h" / "in 2 d"; "due now" once the time has passed. */
function relativeFromNow(iso: string): string {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (minutes <= 0) return 'due now';
  if (minutes < 60) return `in ${minutes} min`;
  if (minutes < 60 * 48) return `in ${Math.round(minutes / 60)} h`;
  return `in ${Math.round(minutes / 1440)} d`;
}

const EMPTY_FORM = {
  title: '', publisher: '', connector_type: 'web_crawl' as ConnectorType, url: '', render: false,
  openbaoPath: '', queryParamsText: '', authStyle: 'bearer', authParamName: '',
  recursive: false, maxDepth: 1, sameDomainOnly: true,
};

/** "key=value" per line -> {key: value}, ignoring blank lines. */
function parseQueryParams(text: string): Record<string, string> | undefined {
  const entries = text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
    const idx = l.indexOf('=');
    return idx === -1 ? [l, ''] : [l.slice(0, idx).trim(), l.slice(idx + 1).trim()];
  });
  return entries.length ? Object.fromEntries(entries) : undefined;
}

const DAY_LABELS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']; // cron day-of-week: 0=Sun..6=Sat
const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

type ScheduleMode = 'none' | 'hourly' | 'weekly' | 'monthly' | 'quarterly' | 'yearly';
const SCHEDULE_MODE_LABEL: Record<ScheduleMode, string> = {
  none: 'No schedule', hourly: 'Hourly',
  weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly', yearly: 'Yearly',
};

/** Compose a 5-field cron from a friendlier picker (iOS Clock-style for
 * weekly) so the person filling the form never has to know cron syntax.
 * `mode: 'none'` returns "" — a manual-only source the worker's cron_due()
 * never auto-triggers (see web_crawler/worker.py's _is_due), only run via
 * "Run Now". Not every real source has a schedule a fixed set of presets can
 * express (a true one-off, or an irregular publication with no fixed
 * cadence) — "no schedule" is a legitimate choice, not a missing feature.
 * Weekly: no days selected, or all 7, both mean "every day" — a wildcard day field.
 * `hourly` is one mode covering BOTH "every N minutes" and "every N hours",
 * via 2 fields instead of a separate mode each: `hourEvery = 0` means "don't
 * step by hours, step by minute instead" (`minuteEvery` becomes the minute
 * step interval); `hourEvery > 0` means "every that many hours, at
 * :minuteEvery past the hour" (`minuteEvery` becomes a literal
 * minute-of-hour, `hourEvery` becomes the hour step interval). */
function buildCron(
  mode: ScheduleMode, time: string, days: number[], dayOfMonth: number, month: number,
  hourEvery: number, minuteEvery: number,
): string {
  if (mode === 'none') return '';
  if (mode === 'hourly') {
    const minute = Math.min(59, Math.max(0, minuteEvery));
    if (hourEvery <= 0) return `*/${Math.min(59, Math.max(1, minute))} * * * *`;
    return `${minute} */${Math.min(23, Math.max(1, hourEvery))} * * *`;
  }
  const [hh, mm] = time.split(':');
  const hour = parseInt(hh, 10) || 0;
  const minute = parseInt(mm, 10) || 0;
  if (mode === 'weekly') {
    const dayField = days.length === 0 || days.length === 7 ? '*' : [...days].sort().join(',');
    return `${minute} ${hour} * * ${dayField}`;
  }
  if (mode === 'monthly') return `${minute} ${hour} ${dayOfMonth} * *`;
  if (mode === 'quarterly') return `${minute} ${hour} ${dayOfMonth} 1,4,7,10 *`;
  return `${minute} ${hour} ${dayOfMonth} ${month} *`; // yearly
}

/** The inverse of buildCron, plus the handful of shapes bulk-import's own
 * word->cron presets produce (hourly, semi-monthly, semi-annual) — so the
 * registry list shows "Every year on Jan 1 at 03:00" instead of a raw
 * "0 3 1 1 *" nobody wants to mentally parse. Anything outside these known
 * shapes (a hand-edited or unusual cron) just falls back to the raw string —
 * never guess at a shape this app doesn't itself generate. */
function cronToHuman(cron: string | null | undefined): string {
  if (!cron || !cron.trim()) return 'No schedule';
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, month, dow] = parts;

  const minStepMatch = min.match(/^\*\/(\d+)$/);
  if (minStepMatch && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return `Every ${minStepMatch[1]} minute${minStepMatch[1] === '1' ? '' : 's'}`;
  }
  const hourStepMatch = hour.match(/^\*\/(\d+)$/);
  if (hourStepMatch && /^\d+$/.test(min) && dom === '*' && month === '*' && dow === '*') {
    return `Every ${hourStepMatch[1]} hour${hourStepMatch[1] === '1' ? '' : 's'} at :${min.padStart(2, '0')}`;
  }
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return min === '0' ? 'Every hour' : `Every hour at :${min.padStart(2, '0')}`;
  }
  if (!/^\d+$/.test(min) || !/^\d+$/.test(hour)) return cron;
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;

  if (dom === '*' && month === '*') {
    if (dow === '*') return `Every day at ${time}`;
    const days = dow.split(',').map(d => DAY_NAMES[parseInt(d, 10)]?.slice(0, 3) ?? d);
    return `Every ${days.join(', ')} at ${time}`;
  }
  if (month === '*' && dow === '*') {
    return `Day ${dom} of every month at ${time}`;
  }
  if (dow === '*' && /^[\d,]+$/.test(month)) {
    const months = month.split(',').map(m => MONTH_NAMES[parseInt(m, 10) - 1] ?? m);
    return months.length > 1
      ? `Day ${dom} of ${months.join('/')} every year at ${time}`
      : `Every year on ${months[0]} ${dom} at ${time}`;
  }
  return cron;
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

  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('none');
  const [scheduleTime, setScheduleTime] = useState('03:00');
  const [scheduleDays, setScheduleDays] = useState<number[]>([]); // [] = every day
  const [scheduleDayOfMonth, setScheduleDayOfMonth] = useState(1);
  const [scheduleMonth, setScheduleMonth] = useState(1);
  const [scheduleHourEvery, setScheduleHourEvery] = useState(0); // 0 = step by minute instead (see buildCron)
  const [scheduleMinuteEvery, setScheduleMinuteEvery] = useState(15);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
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

  // Every time the form opens it starts as Web Page / No schedule, not with
  // whatever a previous, cancelled attempt left selected.
  const resetAddForm = () => {
    setForm(EMPTY_FORM);
    setScheduleMode('none');
    setScheduleTime('03:00');
    setScheduleDays([]);
    setScheduleDayOfMonth(1);
    setScheduleMonth(1);
    setScheduleHourEvery(0);
    setScheduleMinuteEvery(15);
    setErrorMsg(null);
  };

  const [showBulkImport, setShowBulkImport] = useState(false);
  const [bulkText, setBulkText] = useState('');
  const [bulkResult, setBulkResult] = useState<{ created: SourceRow[]; skipped: { title: string; reason: string }[] } | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);

  const [selected, setSelected] = useState<SourceRow | null>(null);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);

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
      || (statusFilters.has('never') && !s.last_run_status);
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
    setRunsLoading(true);
    apiGet<SourceRun[]>(`/api/knowledge/sources/${source.id}/runs`)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setRunsLoading(false));
  };

  const submitAdd = async () => {
    // One line per file — a single registry entry sometimes bundles several
    // distinct files (e.g. a report's separate DOCX narrative + XLSX table
    // workbook); each URL becomes its own source, same as the bulk-import
    // fan-out in sources.py's _entry_to_create_requests, since KBSourceConfig
    // carries exactly one url per source.
    const urls = form.url.split('\n').map(u => u.trim()).filter(Boolean);
    const usesTimeOfDay = scheduleMode !== 'none' && scheduleMode !== 'hourly';
    if (!form.title.trim() || urls.length === 0 || (usesTimeOfDay && !scheduleTime)) {
      setErrorMsg('Title, at least one URL, and a time are required.');
      return;
    }
    setSaving(true);
    setErrorMsg(null);
    const cron = buildCron(
      scheduleMode, scheduleTime, scheduleDays, scheduleDayOfMonth, scheduleMonth,
      scheduleHourEvery, scheduleMinuteEvery,
    );
    try {
      for (const url of urls) {
        const title = urls.length === 1 ? form.title : `${form.title} — ${(url.split('.').pop() || '').toUpperCase()}`;
        await apiMutate('POST', '/api/knowledge/sources', {
          title, publisher: form.publisher, connector_type: form.connector_type,
          url, render: form.render, cron,
          openbao_path: form.openbaoPath || undefined,
          query_params: parseQueryParams(form.queryParamsText),
          auth_style: form.openbaoPath ? form.authStyle : undefined,
          auth_param_name: form.authStyle === 'query_param' ? form.authParamName || undefined : undefined,
          recursive: form.recursive, max_depth: form.maxDepth, same_domain_only: form.sameDomainOnly,
        });
      }
      setShowAddForm(false);
      resetAddForm();
      reload();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Failed to create source.');
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (day: number) => {
    setScheduleDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day].sort());
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

  const handleBatchRun = async () => {
    setIsBatchRunning(true);
    const runIds: string[] = [];
    for (const source of selectedSources) {
      if (isActive(source)) continue;
      const runId = await queueRun(source.id);
      if (runId) runIds.push(runId);
    }
    setIsBatchRunning(false);
    setSelectedIds([]);
    setShowVerification(false);
    onRunQueued?.(runIds);
    reload(true);
  };

  const submitBulkImport = async () => {
    if (!bulkText.trim()) return;
    setBulkSaving(true);
    try {
      const result = await apiMutate<{ created: SourceRow[]; skipped: { title: string; reason: string }[] }>(
        'POST', '/api/knowledge/sources/bulk-import', { yaml_text: bulkText },
      );
      setBulkResult(result);
      reload();
    } catch (e) {
      setBulkResult({ created: [], skipped: [{ title: '?', reason: e instanceof Error ? e.message : 'import failed' }] });
    } finally {
      setBulkSaving(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Globe className="w-4 h-4 text-[#B88719]" />
          <h3 className="text-sm font-bold text-[#1E1B15]">Source Registry</h3>
          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]">{liveSources.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowBulkImport(true)} className="btn-ghost btn-sm">
            <Upload className="w-3.5 h-3.5" /> Bulk Import
          </button>
          <button onClick={() => { if (!showAddForm) resetAddForm(); setShowAddForm(v => !v); }} className="btn-gold !text-xs">
            <Plus className="w-3.5 h-3.5" /> Add Source
          </button>
        </div>
      </div>

      {showAddForm && (
        <div className="content-card content-card--static p-6 space-y-4">
          {errorMsg && <div className="text-xs text-red-600 font-mono">{errorMsg}</div>}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Title</label>
              <input className="ui-input w-full text-xs px-3 py-2" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Publisher</label>
              <input className="ui-input w-full text-xs px-3 py-2" value={form.publisher} onChange={e => setForm(f => ({ ...f, publisher: e.target.value }))} />
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Connector Type</label>
              <div className="flex items-center gap-1.5 flex-wrap">
                {CONNECTOR_TYPES.map(type => {
                  const active = form.connector_type === type;
                  return (
                    <button
                      key={type}
                      type="button"
                      title={CONNECTOR_PILL_DESCRIPTION[type]}
                      onClick={() => setForm(f => ({ ...f, connector_type: type }))}
                      className={`px-3 py-1.5 rounded-full text-[11px] font-bold border transition-colors whitespace-nowrap ${
                        active
                          ? 'bg-[#B88719] border-[#B88719] text-white'
                          : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                      }`}
                    >
                      {CONNECTOR_PILL_LABEL[type]}
                    </button>
                  );
                })}
              </div>
              <div className="text-[10px] font-mono text-[#8A8A8A] mt-1.5">{CONNECTOR_PILL_DESCRIPTION[form.connector_type]}</div>
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">
                URL{form.url.trim().split('\n').filter(Boolean).length > 1 ? 's' : ''}
              </label>
              <textarea
                className="ui-input w-full text-xs px-3 py-2 font-mono h-16 resize-y"
                value={form.url}
                onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
                placeholder="https://...&#10;One per line if this source is really several files (e.g. a report's separate DOCX + XLSX) — each line becomes its own source."
              />
            </div>

            {(form.connector_type === 'rest_api' || form.connector_type === 'sdmx') && (
              <>
                <div className="col-span-2">
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Query Params (one "key=value" per line)</label>
                  <textarea
                    className="ui-input font-mono text-xs h-20 w-full px-3 py-2"
                    value={form.queryParamsText}
                    onChange={e => setForm(f => ({ ...f, queryParamsText: e.target.value }))}
                    placeholder="frequency=M&#10;start_period=2020-01"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">OpenBao Path (optional — auth credential)</label>
                  <input
                    className="ui-input w-full text-xs px-3 py-2 font-mono"
                    value={form.openbaoPath}
                    onChange={e => setForm(f => ({ ...f, openbaoPath: e.target.value }))}
                    placeholder="tenants/<tenant_id>/<key>"
                  />
                </div>
                {form.openbaoPath && (
                  <div className="flex items-end gap-3">
                    <div className="flex-1">
                      <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Auth Style</label>
                      <select
                        className="ui-input w-full text-xs px-3 py-2"
                        value={form.authStyle}
                        onChange={e => setForm(f => ({ ...f, authStyle: e.target.value }))}
                      >
                        <option value="bearer">Bearer header</option>
                        <option value="query_param">Query param (e.g. api_key=)</option>
                      </select>
                    </div>
                    {form.authStyle === 'query_param' && (
                      <div className="flex-1">
                        <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Param Name</label>
                        <input className="ui-input w-full text-xs px-3 py-2 font-mono" value={form.authParamName} onChange={e => setForm(f => ({ ...f, authParamName: e.target.value }))} placeholder="api_key" />
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {(form.connector_type === 'web_crawl' || form.connector_type === 'web_crawl_headless') && (
              <div className="col-span-2 flex items-center gap-4">
                <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                  <input type="checkbox" checked={form.recursive} onChange={e => setForm(f => ({ ...f, recursive: e.target.checked }))} />
                  Follow links (recursive crawl)
                </label>
                {form.recursive && (
                  <>
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      Max depth
                      <input
                        type="number" min={1} max={5}
                        className="ui-input w-16 text-xs px-2 py-1 font-mono"
                        value={form.maxDepth}
                        onChange={e => setForm(f => ({ ...f, maxDepth: parseInt(e.target.value, 10) || 1 }))}
                      />
                    </label>
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      <input type="checkbox" checked={form.sameDomainOnly} onChange={e => setForm(f => ({ ...f, sameDomainOnly: e.target.checked }))} />
                      Same domain only
                    </label>
                  </>
                )}
              </div>
            )}

            <div className="col-span-2">
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Schedule</label>
              <div className="flex items-center gap-1.5 flex-wrap mb-3">
                {(Object.keys(SCHEDULE_MODE_LABEL) as ScheduleMode[]).map(mode => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setScheduleMode(mode)}
                    className={`px-3 py-1.5 rounded-full text-[11px] font-bold border transition-colors whitespace-nowrap ${
                      scheduleMode === mode
                        ? 'bg-[#B88719] border-[#B88719] text-white'
                        : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                    }`}
                  >
                    {SCHEDULE_MODE_LABEL[mode]}
                  </button>
                ))}
              </div>

              {scheduleMode === 'none' && (
                <div className="text-[11px] font-mono text-[#8A8A8A]">
                  Never runs on a timer — only via "Run Now". Good for a true one-off, or a source with no fixed publication cadence.
                </div>
              )}

              {scheduleMode === 'hourly' && (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center gap-4 flex-wrap">
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      Hour
                      <input
                        type="number" min={0} max={23}
                        className="ui-input w-16 text-xs px-2 py-1 font-mono"
                        value={scheduleHourEvery}
                        onChange={e => setScheduleHourEvery(Math.min(23, Math.max(0, parseInt(e.target.value, 10) || 0)))}
                      />
                    </label>
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      Minute
                      <input
                        type="number" min={0} max={59}
                        className="ui-input w-16 text-xs px-2 py-1 font-mono"
                        value={scheduleMinuteEvery}
                        onChange={e => setScheduleMinuteEvery(Math.min(59, Math.max(0, parseInt(e.target.value, 10) || 0)))}
                      />
                    </label>
                  </div>
                  <span className="text-[10px] font-mono text-[#8A8A8A]">
                    Hour = 0 → repeat every [Minute] minutes. Hour &gt; 0 → repeat every [Hour] hours, at that minute past the hour.
                  </span>
                </div>
              )}

              {scheduleMode !== 'none' && scheduleMode !== 'hourly' && (
                <div className="flex items-center gap-4 flex-wrap">
                  <input
                    type="time"
                    className="ui-input text-xs px-3 py-2 font-mono w-32"
                    value={scheduleTime}
                    onChange={e => setScheduleTime(e.target.value)}
                  />

                  {scheduleMode === 'weekly' && (
                    <div className="flex items-center gap-1.5">
                      {DAY_LABELS.map((label, day) => {
                        const active = scheduleDays.includes(day);
                        return (
                          <button
                            key={day}
                            type="button"
                            title={DAY_NAMES[day]}
                            onClick={() => toggleDay(day)}
                            className={`w-7 h-7 rounded-full text-[11px] font-bold border transition-colors ${
                              active
                                ? 'bg-[#B88719] border-[#B88719] text-white'
                                : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                            }`}
                          >
                            {label}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {(scheduleMode === 'monthly' || scheduleMode === 'quarterly' || scheduleMode === 'yearly') && (
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      Day of month
                      <input
                        type="number" min={1} max={31}
                        className="ui-input w-16 text-xs px-2 py-1 font-mono"
                        value={scheduleDayOfMonth}
                        onChange={e => setScheduleDayOfMonth(Math.min(31, Math.max(1, parseInt(e.target.value, 10) || 1)))}
                      />
                    </label>
                  )}

                  {scheduleMode === 'yearly' && (
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      Month
                      <select
                        className="ui-input text-xs px-2 py-1"
                        value={scheduleMonth}
                        onChange={e => setScheduleMonth(parseInt(e.target.value, 10))}
                      >
                        {MONTH_NAMES.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
                      </select>
                    </label>
                  )}
                </div>
              )}

              {scheduleMode !== 'none' && (
                <div className="text-[11px] font-mono text-[#8A8A8A] mt-2">
                  {cronToHuman(buildCron(
                    scheduleMode, scheduleTime, scheduleDays, scheduleDayOfMonth, scheduleMonth,
                    scheduleHourEvery, scheduleMinuteEvery,
                  ))}
                  <div className="mt-1 text-[#5F5F5F]">
                    The schedule starts once you press Run Now: that first run starts immediately, then it repeats on this schedule.
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-ghost btn-sm" onClick={() => { setShowAddForm(false); setErrorMsg(null); }}>Cancel</button>
            <button className="btn-gold !text-xs" onClick={submitAdd} disabled={saving}>
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              {(() => {
                const n = form.url.split('\n').map(u => u.trim()).filter(Boolean).length;
                return n > 1 ? `Create ${n} Sources` : 'Create';
              })()}
            </button>
          </div>
        </div>
      )}

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
              <th className="px-6 py-5">Schedule</th>
              <th className="px-6 py-5">Next Run</th>
              <th className="px-6 py-5">Last Run</th>
              <th className="px-8 py-5 text-right font-black">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A] text-[#111111]">
            {isLoading ? (
              <tr><td colSpan={7} className="px-8 py-12 text-center bg-white">
                <div className="flex items-center justify-center gap-2 text-[#5F5F5F] text-xs font-mono">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              </td></tr>
            ) : visible.length === 0 ? (
              <tr><td colSpan={7} className="px-8 py-12 text-center text-[#5F5F5F] text-xs font-mono bg-white">
                {liveSources.length === 0
                  ? 'No sources registered. Use "Add Source" or "Bulk Import" to register one.'
                  : 'No sources match the current filters.'}
              </td></tr>
            ) : groups.map(group => (
              <React.Fragment key={group.key}>
                <tr>
                  <td colSpan={7} className="px-6 py-2.5 bg-[#FFF9E8] text-[10px] font-black uppercase tracking-widest text-[#8A5A00]">
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
                      </td>
                      <td className="px-8 py-6 text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-2.5">
                          {active ? (
                            <span className="text-[10px] font-mono font-bold text-[#B88719] uppercase tracking-wide flex items-center justify-end gap-1.5">
                              <span className="w-3 h-3 rounded-full border-2 border-[#B88719] border-t-[#B88719] animate-spin" />
                              {source.last_run_status === 'queued' ? 'Queued...' : 'Running...'}
                            </span>
                          ) : (
                            <button onClick={() => runNow(source.id)} className="btn-ghost btn-sm" title="Run now">
                              Run Now
                              <ArrowRight className="w-3 h-3" />
                            </button>
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

      {/* ── Run history drawer ── */}
      <DetailDrawer isOpen={!!selected} onClose={() => setSelected(null)} title={selected?.title || 'Source'} subtitle="Run history" icon={Globe}>
        {runsLoading ? (
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
                          {isActive(source) ? 'Already queued or running — will be skipped' : `ID: ${source.id}`}
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

      {showBulkImport && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6" onClick={() => { setShowBulkImport(false); setBulkResult(null); }}>
          <div className="content-card content-card--static p-6 space-y-4 max-w-2xl w-full max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-bold text-[#1E1B15]">Bulk Import Sources (YAML)</h4>
              <button onClick={() => { setShowBulkImport(false); setBulkResult(null); }}><X className="w-4 h-4" /></button>
            </div>
            <p className="text-xs text-[#5F5F5F]">
              Paste a source registry's YAML (a top-level <code>sources:</code> list, e.g. the ANKT registry).
              <code>http-file</code> / <code>web-crawl</code> / <code>web-crawl-headless</code> / <code>rss</code> / <code>rest-api</code> / <code>sdmx</code> rows
              are created — others (manual-upload, bulk-parquet) are reported below with a reason.
            </p>
            <textarea
              className="ui-input font-mono text-xs h-64 w-full px-3 py-2"
              value={bulkText}
              onChange={e => setBulkText(e.target.value)}
              placeholder="sources:&#10;  - id: my-source&#10;    connector: web-crawl&#10;    landing_url: https://...&#10;    schedule: daily"
            />
            {bulkResult && (
              <div className="text-xs space-y-2 max-h-40 overflow-auto border-t border-[#EDE6D3] pt-2">
                <div className="text-green-700 font-bold">Created: {bulkResult.created.length}</div>
                {bulkResult.skipped.length > 0 && (
                  <div>
                    <div className="text-amber-700 font-bold">Skipped: {bulkResult.skipped.length}</div>
                    {bulkResult.skipped.map((s, i) => (
                      <div key={i} className="font-mono text-[#5F5F5F]">— {s.title}: {s.reason}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button className="btn-ghost btn-sm" onClick={() => { setShowBulkImport(false); setBulkResult(null); }}>Close</button>
              <button className="btn-gold !text-xs" onClick={submitBulkImport} disabled={bulkSaving}>
                {bulkSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />} Import
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
