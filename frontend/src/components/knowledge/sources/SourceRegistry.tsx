import React, { useState } from 'react';
import { Globe, Plus, Play, Trash2, X, Loader2, Upload, Clock } from 'lucide-react';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { DetailDrawer } from '../../shared/DetailDrawer';

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
  last_run_status: 'running' | 'success' | 'failed' | null;
  last_run_at: string | null;
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
};

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

type ScheduleMode = 'none' | 'minutes' | 'weekly' | 'monthly' | 'quarterly' | 'yearly';
const SCHEDULE_MODE_LABEL: Record<ScheduleMode, string> = {
  none: 'No schedule', minutes: 'Every N minutes',
  weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly', yearly: 'Yearly',
};

/** Compose a 5-field cron from a friendlier picker (iOS Clock-style for
 * weekly) so the person filling the form never has to know cron syntax.
 * `mode: 'none'` returns "" — a manual-only source the worker's cron_due()
 * never auto-triggers (see web_crawler/worker.py's _is_due), only run via
 * "Run Now". Not every real source has a schedule a fixed set of presets can
 * express (a true one-off, or an irregular publication with no fixed
 * cadence) — "no schedule" is a legitimate choice, not a missing feature.
 * Weekly: no days selected, or all 7, both mean "every day" — `*`.
 * `hourly` (weekly mode only) drops the hour from the time picker — cron's
 * hour field becomes `*` — so "hourly" isn't its own mode, just "weekly"
 * with the hour ignored; day-of-week selection still applies (e.g. "every
 * hour, weekdays only" is a real, valid combination this allows). */
function buildCron(
  mode: ScheduleMode, time: string, days: number[], dayOfMonth: number, month: number,
  everyMinutes: number, hourly: boolean,
): string {
  if (mode === 'none') return '';
  if (mode === 'minutes') return `*/${Math.min(59, Math.max(1, everyMinutes))} * * * *`;
  const [hh, mm] = time.split(':');
  const hour = hourly ? '*' : (parseInt(hh, 10) || 0);
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

  const stepMatch = min.match(/^\*\/(\d+)$/);
  if (stepMatch && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return `Every ${stepMatch[1]} minute${stepMatch[1] === '1' ? '' : 's'}`;
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

export const SourceRegistry = () => {
  const { items: sources, page, totalPages, hasNext, isLoading, canJump, goTo, reload } =
    useCursorPagination<SourceRow>('/api/knowledge/sources', {}, []);

  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('weekly');
  const [scheduleTime, setScheduleTime] = useState('03:00');
  const [scheduleDays, setScheduleDays] = useState<number[]>([]); // [] = every day
  const [scheduleDayOfMonth, setScheduleDayOfMonth] = useState(1);
  const [scheduleMonth, setScheduleMonth] = useState(1);
  const [scheduleEveryMinutes, setScheduleEveryMinutes] = useState(15);
  const [scheduleHourly, setScheduleHourly] = useState(false); // weekly mode: ignore the hour, fire every hour
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);

  const [showBulkImport, setShowBulkImport] = useState(false);
  const [bulkText, setBulkText] = useState('');
  const [bulkResult, setBulkResult] = useState<{ created: SourceRow[]; skipped: { title: string; reason: string }[] } | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);

  const [selected, setSelected] = useState<SourceRow | null>(null);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);

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
    if (!form.title.trim() || urls.length === 0 || (scheduleMode !== 'none' && !scheduleTime)) {
      setErrorMsg('Title, at least one URL, and a time are required.');
      return;
    }
    setSaving(true);
    setErrorMsg(null);
    const cron = buildCron(
      scheduleMode, scheduleTime, scheduleDays, scheduleDayOfMonth, scheduleMonth,
      scheduleEveryMinutes, scheduleHourly,
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
      setForm(EMPTY_FORM);
      setScheduleMode('weekly');
      setScheduleTime('03:00');
      setScheduleDays([]);
      setScheduleDayOfMonth(1);
      setScheduleMonth(1);
      setScheduleEveryMinutes(15);
      setScheduleHourly(false);
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

  const deleteSource = async (id: string) => {
    await apiMutate('DELETE', `/api/knowledge/sources/${id}`);
    setPendingDeleteId(null);
    reload();
  };

  const runNow = async (id: string) => {
    setRunningId(id);
    try {
      await apiMutate('POST', `/api/knowledge/sources/${id}/run-now`);
    } finally {
      setRunningId(null);
    }
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
          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]">{sources.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowBulkImport(true)} className="btn-ghost btn-sm">
            <Upload className="w-3.5 h-3.5" /> Bulk Import
          </button>
          <button onClick={() => setShowAddForm(v => !v)} className="btn-gold !text-xs">
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

              {scheduleMode === 'minutes' && (
                <div className="flex items-center gap-4 flex-wrap">
                  <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                    Every
                    <input
                      type="number" min={1} max={59}
                      className="ui-input w-16 text-xs px-2 py-1 font-mono"
                      value={scheduleEveryMinutes}
                      onChange={e => setScheduleEveryMinutes(Math.min(59, Math.max(1, parseInt(e.target.value, 10) || 1)))}
                    />
                    minutes
                  </label>
                </div>
              )}

              {scheduleMode !== 'none' && scheduleMode !== 'minutes' && (
                <div className="flex items-center gap-4 flex-wrap">
                  {scheduleHourly ? (
                    <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                      At minute
                      <input
                        type="number" min={0} max={59}
                        className="ui-input w-16 text-xs px-2 py-1 font-mono"
                        value={parseInt(scheduleTime.split(':')[1], 10) || 0}
                        onChange={e => {
                          const mm = Math.min(59, Math.max(0, parseInt(e.target.value, 10) || 0));
                          setScheduleTime(`00:${String(mm).padStart(2, '0')}`);
                        }}
                      />
                      of every hour
                    </label>
                  ) : (
                    <input
                      type="time"
                      className="ui-input text-xs px-3 py-2 font-mono w-32"
                      value={scheduleTime}
                      onChange={e => setScheduleTime(e.target.value)}
                    />
                  )}

                  {scheduleMode === 'weekly' && (
                    <>
                      <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
                        <input type="checkbox" checked={scheduleHourly} onChange={e => setScheduleHourly(e.target.checked)} />
                        Hourly
                      </label>
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
                    </>
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
                    scheduleEveryMinutes, scheduleHourly,
                  ))}
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

      <div className="content-card content-card--static overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-black border-b border-[#BFA66A] bg-[#FCFBF7]">
              <th className="px-6 py-5">Title</th>
              <th className="px-6 py-5">Connector</th>
              <th className="px-6 py-5">Schedule</th>
              <th className="px-6 py-5">Last Run</th>
              <th className="px-8 py-5 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A] text-[#111111]">
            {isLoading ? (
              <tr><td colSpan={5} className="px-8 py-12 text-center bg-white">
                <div className="flex items-center justify-center gap-2 text-[#5F5F5F] text-xs font-mono">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              </td></tr>
            ) : sources.length === 0 ? (
              <tr><td colSpan={5} className="px-8 py-12 text-center text-[#5F5F5F] text-xs font-mono bg-white">
                No sources registered. Use "Add Source" or "Bulk Import" to register one.
              </td></tr>
            ) : sources.map(source => (
              <tr key={source.id} className="group transition-colors cursor-pointer bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/60" onClick={() => openRuns(source)}>
                <td className="px-6 py-6">
                  <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors">{source.title}</div>
                  <div className="text-[10px] font-mono text-[#8A5A00] truncate max-w-xs">{source.url}</div>
                </td>
                <td className="px-6 py-6 font-mono text-xs text-[#3F3F3F]">{CONNECTOR_LABEL[source.connector_type]}</td>
                <td className="px-6 py-6 font-mono text-xs text-[#5F5F5F]">
                  <span className="inline-flex items-center gap-1" title={source.cron || undefined}><Clock className="w-3 h-3" />{cronToHuman(source.cron)}</span>
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
                    <button onClick={() => runNow(source.id)} className="btn-ghost btn-sm" disabled={runningId === source.id} title="Run now">
                      {runningId === source.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />} Run Now
                    </button>
                    {pendingDeleteId === source.id ? (
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
                    )}
                  </div>
                </td>
              </tr>
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
