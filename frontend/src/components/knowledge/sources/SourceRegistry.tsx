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

/** Compose a 5-field cron from a friendlier "time + repeat days" picker (iOS
 * Clock-style) so the person filling the form never has to know cron syntax.
 * No days selected, or all 7, both mean "every day" — `*` in the day field. */
function buildCron(time: string, days: number[]): string {
  const [hh, mm] = time.split(':');
  const hour = parseInt(hh, 10) || 0;
  const minute = parseInt(mm, 10) || 0;
  const dayField = days.length === 0 || days.length === 7 ? '*' : [...days].sort().join(',');
  return `${minute} ${hour} * * ${dayField}`;
}

export const SourceRegistry = () => {
  const { items: sources, page, totalPages, hasNext, isLoading, canJump, goTo, reload } =
    useCursorPagination<SourceRow>('/api/knowledge/sources', {}, []);

  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [scheduleTime, setScheduleTime] = useState('03:00');
  const [scheduleDays, setScheduleDays] = useState<number[]>([]); // [] = every day
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
    if (!form.title.trim() || !form.url.trim() || !scheduleTime) {
      setErrorMsg('Title, URL and a time are required.');
      return;
    }
    setSaving(true);
    setErrorMsg(null);
    try {
      await apiMutate('POST', '/api/knowledge/sources', {
        title: form.title, publisher: form.publisher, connector_type: form.connector_type,
        url: form.url, render: form.render, cron: buildCron(scheduleTime, scheduleDays),
        openbao_path: form.openbaoPath || undefined,
        query_params: parseQueryParams(form.queryParamsText),
        auth_style: form.openbaoPath ? form.authStyle : undefined,
        auth_param_name: form.authStyle === 'query_param' ? form.authParamName || undefined : undefined,
        recursive: form.recursive, max_depth: form.maxDepth, same_domain_only: form.sameDomainOnly,
      });
      setShowAddForm(false);
      setForm(EMPTY_FORM);
      setScheduleTime('03:00');
      setScheduleDays([]);
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
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">URL</label>
              <input className="ui-input w-full text-xs px-3 py-2 font-mono" value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))} placeholder="https://..." />
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
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Schedule (server time)</label>
              <div className="flex items-center gap-4 flex-wrap">
                <input
                  type="time"
                  className="ui-input text-xs px-3 py-2 font-mono w-32"
                  value={scheduleTime}
                  onChange={e => setScheduleTime(e.target.value)}
                />
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
                <span className="text-[11px] font-mono text-[#8A8A8A]">
                  {scheduleDays.length === 0 || scheduleDays.length === 7
                    ? 'Every day'
                    : scheduleDays.map(d => DAY_NAMES[d].slice(0, 3)).join(', ')}
                  {' at '}{scheduleTime}
                </span>
              </div>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn-ghost btn-sm" onClick={() => { setShowAddForm(false); setErrorMsg(null); }}>Cancel</button>
            <button className="btn-gold !text-xs" onClick={submitAdd} disabled={saving}>
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />} Create
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
                  <span className="inline-flex items-center gap-1"><Clock className="w-3 h-3" />{source.cron || '—'}</span>
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
