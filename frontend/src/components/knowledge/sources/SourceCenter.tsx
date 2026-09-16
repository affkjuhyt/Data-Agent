/**
 * Nguồn dữ liệu — the external source catalogue (POC-PLAN demo scene 1).
 *
 * Lives inside Knowledge Ops beside Warehouses because both are external data
 * connections a data engineer configures; a separate island would mean a second
 * navigation and a second mental model for one job.
 *
 * Everything shown comes from ankt.source and ankt.connector_run, so adding a
 * source is a row rather than a release. The connector column names a PROTOCOL
 * (sdmx, rest-json) — the point of the registry is that the thirty-ninth source
 * needs no new code, and this column is where that shows.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Rss, Play, Loader2, CheckCircle2, AlertTriangle, XCircle, Clock, ChevronRight, Plus, Trash2, Pencil } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { formatDateTime, formatNumber, formatRelative } from '../../../lib/formatVN';
import { SourceWizard } from './SourceWizard';

export interface ConnectorRun {
  run_id: string;
  started_at: string | null;
  finished_at: string | null;
  status: 'running' | 'ok' | 'partial' | 'failed';
  fetched: number;
  inserted: number;
  skipped: number;
  error: string | null;
  warnings?: string[];
  triggered_by?: string;
}

export interface SourceRow {
  source_id: string;
  code: string;
  title: string;
  publisher: string | null;
  connector: string;
  schedule: string | null;
  licence: string | null;
  enabled: boolean;
  series_count: number;
  last_run: ConnectorRun | null;
}

export interface SeriesRow {
  binding_id: string;
  series_key: string;
  enabled: boolean;
  variable_code: string;
  concept: string;
  unit: string;
  currency: string | null;
  perimeter: string | null;
  observation_count: number;
  latest_period: string | null;
}

const STATUS = {
  ok:      { icon: CheckCircle2,  label: 'Thành công', cls: 'text-emerald-600 bg-emerald-50' },
  partial: { icon: AlertTriangle, label: 'Một phần',   cls: 'text-amber-600 bg-amber-50' },
  failed:  { icon: XCircle,       label: 'Thất bại',   cls: 'text-red-600 bg-red-50' },
  running: { icon: Loader2,       label: 'Đang chạy',  cls: 'text-blue-600 bg-blue-50' },
} as const;

function RunBadge({ run }: { run: ConnectorRun | null }) {
  if (!run) {
    return <span className="text-[11px] text-[#8A8A8A]">chưa chạy lần nào</span>;
  }
  const meta = STATUS[run.status];
  const Icon = meta.icon;
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium', meta.cls)}>
      <Icon className={cn('w-3 h-3', run.status === 'running' && 'animate-spin')} />
      {meta.label}
    </span>
  );
}

export function SourceCenter() {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [series, setSeries] = useState<Record<string, SeriesRow[]>>({});
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<SourceRow[]>('/api/knowledge/sources');
      setSources(data ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được danh mục nguồn');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const runNow = async (source: SourceRow) => {
    setRunning(source.source_id);
    try {
      // The server answers 202 and works in the background — fetching seven
      // series from two publishers takes longer than the client's mutation
      // timeout. The run row is the handle, so poll it rather than wait.
      await apiMutate('POST', `/api/knowledge/sources/${source.source_id}/runs`, {});
      for (let attempt = 0; attempt < 30; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const runs = await apiGet<ConnectorRun[]>(
          `/api/knowledge/sources/${source.source_id}/runs`, { limit: 1 });
        if (runs?.[0] && runs[0].status !== 'running') break;
      }
      // Re-read rather than patching the row locally: the run wrote rows the
      // server owns, and guessing their contents here is how a screen starts
      // disagreeing with the database it is displaying.
      await load();
      setSeries((prev) => ({ ...prev, [source.source_id]: [] }));
      if (expanded === source.source_id) {
        const rows = await apiGet<SeriesRow[]>(
          `/api/knowledge/sources/${source.source_id}/series`);
        setSeries((prev) => ({ ...prev, [source.source_id]: rows ?? [] }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Chạy nguồn thất bại');
    } finally {
      setRunning(null);
    }
  };

  const toggle = async (source: SourceRow) => {
    const next = expanded === source.source_id ? null : source.source_id;
    setExpanded(next);
    if (next && !series[next]) {
      const rows = await apiGet<SeriesRow[]>(`/api/knowledge/sources/${next}/series`);
      setSeries((prev) => ({ ...prev, [next]: rows ?? [] }));
    }
  };

  const removeSource = async (source: SourceRow) => {
    // Observations survive on purpose — they are published facts with their own
    // artifacts, not a property of the catalogue row that fetched them.
    if (!window.confirm(
      `Xoá nguồn "${source.title}"?\n\nCác quan sát đã công bố KHÔNG bị xoá.`)) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/sources/${source.source_id}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Xoá không thành công');
    }
  };

  if (loading) {
    return <div className="flex items-center gap-2 p-8 text-[#5F5F5F]">
      <Loader2 className="w-4 h-4 animate-spin" /> Đang tải danh mục nguồn…
    </div>;
  }

  return (
    <div className="animate-in fade-in duration-300">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold text-[#111111] flex items-center gap-2">
            <Rss className="w-4 h-4" /> Nguồn dữ liệu
          </h2>
          <p className="text-[12px] text-[#5F5F5F] mt-0.5">
            {sources.length} nguồn · {sources.reduce((n, s) => n + s.series_count, 0)} chuỗi số liệu
          </p>
        </div>
        <button
          onClick={() => setWizardOpen(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#111111] text-white text-[12px]"
        >
          <Plus className="w-3.5 h-3.5" /> Thêm nguồn
        </button>
      </div>

      {(wizardOpen || editing) && (
        <SourceWizard
          sourceId={editing ?? undefined}
          onClose={() => { setWizardOpen(false); setEditing(null); }}
          onSaved={() => void load()}
        />
      )}

      {error && (
        <div className="mb-3 px-3 py-2 rounded bg-red-50 text-red-700 text-[12px]">{error}</div>
      )}

      {sources.length === 0 && (
        <div className="p-8 text-center text-[13px] text-[#5F5F5F]">
          Chưa có nguồn nào. Chạy <code className="font-mono">python -m services.ankt_connectors seed</code> để
          nạp danh mục khởi tạo.
        </div>
      )}

      <div className="space-y-2">
        {sources.map((source) => {
          const isOpen = expanded === source.source_id;
          return (
            <div key={source.source_id} className="content-card content-card--static overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3">
                <button
                  onClick={() => void toggle(source)}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left"
                  aria-expanded={isOpen}
                >
                  <ChevronRight className={cn('w-4 h-4 text-[#8A8A8A] transition-transform', isOpen && 'rotate-90')} />
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-[#111111] truncate">{source.title}</div>
                    <div className="text-[11px] text-[#8A8A8A] truncate">
                      {source.publisher} · <span className="font-mono">{source.code}</span>
                    </div>
                  </div>
                </button>

                <span className="px-2 py-0.5 rounded bg-[#F3F3F3] text-[11px] font-mono text-[#5F5F5F]">
                  {source.connector}
                </span>
                <span className="text-[11px] text-[#5F5F5F] w-16 text-right">
                  {source.series_count} chuỗi
                </span>

                <div className="w-44 text-right">
                  <RunBadge run={source.last_run} />
                  {source.last_run?.started_at && (
                    <div className="text-[10px] text-[#8A8A8A] mt-0.5" title={formatDateTime(source.last_run.started_at)}>
                      {formatRelative(source.last_run.started_at)}
                      {' · '}
                      {formatNumber(source.last_run.inserted)} mới / {formatNumber(source.last_run.fetched)}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => void runNow(source)}
                  disabled={running === source.source_id}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#E0E0E0] text-[12px] hover:bg-[#F7F7F7] disabled:opacity-50"
                >
                  {running === source.source_id
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <Play className="w-3.5 h-3.5" />}
                  Chạy
                </button>

                <button
                  onClick={() => setEditing(source.source_id)}
                  title="Sửa nguồn"
                  className="p-1 rounded text-[#8A8A8A] hover:text-[#111111] hover:bg-[#F3F3F3]"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>

                <button
                  onClick={() => void removeSource(source)}
                  title="Xoá nguồn (quan sát đã công bố vẫn giữ nguyên)"
                  className="p-1 rounded text-[#8A8A8A] hover:text-red-600 hover:bg-red-50"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>

              {source.last_run?.error && (
                <div className="px-4 pb-2 text-[11px] text-red-600">{source.last_run.error}</div>
              )}

              {isOpen && (
                <div className="border-t border-[#EFEFEF] bg-[#FBFBFB] px-4 py-3">
                  <div className="flex items-center gap-2 text-[11px] text-[#5F5F5F] mb-2">
                    <Clock className="w-3 h-3" />
                    Lịch: {source.schedule ?? 'thủ công'}
                    {source.licence && <> · Giấy phép: {source.licence}</>}
                  </div>
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="text-[11px] text-[#8A8A8A] text-left">
                        <th className="py-1 font-medium">Biến</th>
                        <th className="py-1 font-medium">Mã chuỗi ở nguồn</th>
                        <th className="py-1 font-medium">Phạm vi</th>
                        <th className="py-1 font-medium text-right">Quan sát</th>
                        <th className="py-1 font-medium text-right">Kỳ mới nhất</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(series[source.source_id] ?? []).map((row) => (
                        <tr key={row.binding_id} className="border-t border-[#F0F0F0]">
                          <td className="py-1.5">
                            <div className="text-[#111111]">{row.concept}</div>
                            <div className="text-[10px] text-[#8A8A8A] font-mono">{row.variable_code}</div>
                          </td>
                          <td className="py-1.5 font-mono text-[11px] text-[#5F5F5F]">{row.series_key}</td>
                          <td className="py-1.5 text-[11px] text-[#5F5F5F]">{row.perimeter ?? '—'}</td>
                          <td className="py-1.5 text-right font-mono">{formatNumber(row.observation_count)}</td>
                          <td className="py-1.5 text-right font-mono">{row.latest_period ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
