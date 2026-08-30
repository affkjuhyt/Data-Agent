/**
 * Sandbox Executor — live ops view (docs/1.md "Kế hoạch 1" mục h: "stream
 * trạng thái/stdout-stderr của lần exec đang chạy ra một kênh quan sát
 * được (dashboard/UI)... hữu ích để vận hành nhìn thấy job đang chạy gì").
 *
 * Talks to workflow-runtime's relay endpoints (routers/sandbox_ops.py),
 * never to sandbox-executor directly — it's internal-only, no port
 * exposed to the host/Kong (doc §1.2 hub-and-spoke isolation).
 *
 *   GET  /api/sandbox/status              -> pool health + recent jobs (polled)
 *   GET  /api/sandbox/job/{kind}/{id}      -> full detail for an already-finished job
 *   GET  /api/sandbox/stream/{kind}/{id}   -> SSE: status transitions + live stdout/stderr
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, Box, Cpu, Loader2, ShieldAlert, X,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { WORKFLOW_RUNTIME_URL } from '../../../lib/config';

interface JobSummary {
  job_id: string;
  kind: 'python' | 'sql';
  status: 'queued' | 'running' | 'done' | 'rejected' | 'error';
  age_s: number;
  duration_s: number | null;
  error: string | null;
}

interface PoolStats {
  size: number;
  idle: number;
  busy: number;
  egress_enabled: boolean;
  runtime: string;
  circuit_breaker: { state: 'closed' | 'open' | 'half_open'; enforce: boolean };
}

interface SandboxStatus {
  pool: PoolStats;
  jobs: { python: JobSummary[]; sql: JobSummary[] };
}

interface OutputLine {
  stream: 'stdout' | 'stderr';
  text: string;
}

const POLL_INTERVAL_MS = 2500;

const STATUS_STYLE: Record<JobSummary['status'], string> = {
  queued:   'bg-[#FFF9E8] border-[#BFA66A] text-[#3F3F3F]',
  running:  'bg-blue-50 border-blue-200 text-blue-700',
  done:     'bg-emerald-50 border-emerald-200 text-emerald-700',
  rejected: 'bg-amber-50 border-amber-200 text-amber-700',
  error:    'bg-red-50 border-red-200 text-red-700',
};

const STATUS_DOT: Record<JobSummary['status'], string> = {
  queued:   'bg-slate-400',
  running:  'bg-blue-500 animate-pulse',
  done:     'bg-emerald-500',
  rejected: 'bg-amber-500',
  error:    'bg-red-500',
};

const BREAKER_STYLE: Record<PoolStats['circuit_breaker']['state'], string> = {
  closed:     'bg-emerald-50 border-emerald-200 text-emerald-700',
  half_open:  'bg-amber-50 border-amber-200 text-amber-700',
  open:       'bg-red-50 border-red-200 text-red-700',
};

function formatAge(ageS: number): string {
  if (ageS < 60) return `${Math.round(ageS)}s`;
  if (ageS < 3600) return `${Math.round(ageS / 60)}m`;
  return `${Math.round(ageS / 3600)}h`;
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${WORKFLOW_RUNTIME_URL}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

const SandboxQueuePanel: React.FC = () => {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobSummary | null>(null);
  const [liveOutput, setLiveOutput] = useState<OutputLine[]>([]);
  const [liveStatus, setLiveStatus] = useState<JobSummary['status'] | null>(null);
  const [finalResult, setFinalResult] = useState<Record<string, unknown> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const data = await fetchJson<SandboxStatus>('/api/sandbox/status');
      setStatus(data);
      setStatusError(null);
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : 'sandbox-executor unreachable');
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    const id = setInterval(refreshStatus, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshStatus]);

  // Subscribe to the selected job — live SSE while it's still in flight,
  // a single detail fetch once it's already terminal.
  useEffect(() => {
    abortRef.current?.abort();
    setLiveOutput([]);
    setFinalResult(null);
    if (!selectedJob) return;

    setLiveStatus(selectedJob.status);

    if (selectedJob.status === 'done' || selectedJob.status === 'rejected' || selectedJob.status === 'error') {
      fetchJson<Record<string, unknown>>(`/api/sandbox/job/${selectedJob.kind}/${selectedJob.job_id}`)
        .then(setFinalResult)
        .catch((e) => setFinalResult({ error: e instanceof Error ? e.message : String(e) }));
      return;
    }

    const abort = new AbortController();
    abortRef.current = abort;

    (async () => {
      try {
        const res = await fetch(
          `${WORKFLOW_RUNTIME_URL}/api/sandbox/stream/${selectedJob.kind}/${selectedJob.job_id}`,
          { signal: abort.signal },
        );
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data:')) continue; // skip ": heartbeat" comments
            let data: any;
            try { data = JSON.parse(line.slice(5).trim()); } catch { continue; }
            if (data.stream && typeof data.text === 'string') {
              setLiveOutput((prev) => [...prev, { stream: data.stream, text: data.text }]);
            } else if (data.status) {
              setLiveStatus(data.status);
              if (data.status !== 'queued' && data.status !== 'running') {
                setFinalResult(data);
                refreshStatus(); // pool/job list just changed (job finished)
              }
            }
          }
        }
      } catch {
        if (!abort.signal.aborted) setFinalResult({ error: 'stream connection lost' });
      }
    })();

    return () => abort.abort();
  }, [selectedJob, refreshStatus]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const allJobs = status ? [...status.jobs.python, ...status.jobs.sql] : [];

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex-1 flex flex-col min-w-0">
        {/* Pool health strip */}
        <div className="p-5 border-b border-[#BFA66A] bg-white grid grid-cols-2 sm:grid-cols-5 gap-3">
          {statusError ? (
            <div className="col-span-5 flex items-center gap-2 text-red-600 text-xs font-bold px-2">
              <ShieldAlert className="w-4 h-4" /> {statusError} — sandbox-executor/workflow-runtime relay unreachable
            </div>
          ) : (
            <>
              <StatTile icon={Box} label="Pool size" value={status?.pool.size ?? '—'} />
              <StatTile icon={Cpu} label="Idle / Busy" value={status ? `${status.pool.idle} / ${status.pool.busy}` : '—'} />
              <div className="p-3.5 bg-[#FAFAF5] border border-[#BFA66A] rounded-xl">
                <div className="flex items-center gap-1.5 text-[#5F5F5F] mb-2">
                  <Activity className="w-3.5 h-3.5 text-[#B88719]" />
                  <span className="text-[9px] font-black uppercase tracking-widest">Circuit breaker</span>
                </div>
                <span className={cn(
                  'inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest border',
                  status ? BREAKER_STYLE[status.pool.circuit_breaker.state] : 'bg-[#FFF9E8] border-[#BFA66A] text-[#5F5F5F]',
                )}>
                  {status?.pool.circuit_breaker.state ?? '—'}
                </span>
                {status && !status.pool.circuit_breaker.enforce && (
                  <div className="text-[9px] text-[#5F5F5F] mt-1 italic">log-only</div>
                )}
              </div>
              <StatTile icon={Box} label="Egress network" value={status?.pool.egress_enabled ? 'ON' : 'OFF'} />
              <StatTile icon={Cpu} label="Runtime" value={status?.pool.runtime ?? '—'} />
            </>
          )}
        </div>

        {/* Job list */}
        <div className="flex-1 overflow-auto">
          {allJobs.length === 0 ? (
            <div className="p-10 text-center text-sm text-[#5F5F5F]">
              {status ? 'No sandbox jobs yet.' : 'Loading…'}
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest border-b border-[#BFA66A] bg-[#FAFAF5]">
                  <th className="px-6 py-3 text-left">Job</th>
                  <th className="px-4 py-3 text-left">Kind</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Age / Duration</th>
                </tr>
              </thead>
              <tbody>
                {allJobs.map((job) => (
                  <tr
                    key={job.job_id}
                    onClick={() => setSelectedJob(job)}
                    className={cn(
                      'group border-b border-[#BFA66A] hover:bg-[#FBF8F0] transition-all cursor-pointer',
                      selectedJob?.job_id === job.job_id ? 'bg-[#F3E2A7]/30' : 'bg-white',
                    )}
                  >
                    <td className="px-6 py-3 text-xs font-mono text-[#5A5A5A]">{job.job_id.slice(0, 12)}…</td>
                    <td className="px-4 py-3 text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest">{job.kind}</td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border',
                        STATUS_STYLE[job.status],
                      )}>
                        <span className={cn('w-1 h-1 rounded-full', STATUS_DOT[job.status])} />
                        {job.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-xs font-mono text-[#5A5A5A]">
                      {job.duration_s != null ? `${job.duration_s}s` : `${formatAge(job.age_s)} ago`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Detail panel */}
      {selectedJob && (
        <div className="w-[420px] shrink-0 border-l border-[#BFA66A] bg-white flex flex-col">
          <div className="p-5 border-b border-[#BFA66A] bg-[#FAFAF5] flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-black text-[#B88719] uppercase tracking-widest">{selectedJob.kind} job</span>
                <span className="text-xs font-mono text-[#5A5A5A]">#{selectedJob.job_id.slice(0, 12)}</span>
              </div>
              <div className="flex items-center gap-1.5">
                {(liveStatus === 'queued' || liveStatus === 'running') && <Loader2 className="w-3.5 h-3.5 text-blue-600 animate-spin" />}
                <span className="text-sm font-bold text-[#111111] capitalize">{liveStatus ?? selectedJob.status}</span>
              </div>
            </div>
            <button
              onClick={() => setSelectedJob(null)}
              className="p-2 hover:bg-[#F3E2A7] rounded-lg transition-all text-[#5F5F5F] hover:text-[#111111]"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex-1 overflow-auto p-4 space-y-3">
            {liveOutput.length > 0 && (
              <div className="p-3 bg-[#111111] rounded-xl font-mono text-[11px] leading-relaxed max-h-64 overflow-auto">
                {liveOutput.map((line, i) => (
                  <div key={i} className={line.stream === 'stderr' ? 'text-red-400' : 'text-[#D9B86C]'}>
                    {line.text}
                  </div>
                ))}
              </div>
            )}

            {finalResult && (
              <div className="space-y-2">
                {'error' in finalResult && finalResult.error != null && (
                  <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span className="font-mono break-words">{String(finalResult.error)}</span>
                  </div>
                )}
                {typeof finalResult.stdout === 'string' && finalResult.stdout && (
                  <div>
                    <div className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest mb-1">stdout</div>
                    <pre className="p-3 bg-[#111111] text-[#D9B86C] rounded-xl font-mono text-[11px] whitespace-pre-wrap break-words max-h-56 overflow-auto">
                      {finalResult.stdout as string}
                    </pre>
                  </div>
                )}
                {typeof finalResult.stderr === 'string' && finalResult.stderr && (
                  <div>
                    <div className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest mb-1">stderr</div>
                    <pre className="p-3 bg-[#111111] text-red-400 rounded-xl font-mono text-[11px] whitespace-pre-wrap break-words max-h-56 overflow-auto">
                      {finalResult.stderr as string}
                    </pre>
                  </div>
                )}
                {Array.isArray(finalResult.rows) && (
                  <div>
                    <div className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest mb-1">
                      rows ({(finalResult.row_count as number) ?? finalResult.rows.length})
                    </div>
                    <pre className="p-3 bg-[#111111] text-[#D9B86C] rounded-xl font-mono text-[10px] whitespace-pre-wrap break-words max-h-56 overflow-auto">
                      {JSON.stringify(finalResult.rows, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}

            {!finalResult && liveOutput.length === 0 && (liveStatus === 'queued' || liveStatus === 'running') && (
              <div className="text-xs text-[#5F5F5F] italic">Waiting for output…</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const StatTile: React.FC<{ icon: typeof Box; label: string; value: React.ReactNode }> = ({ icon: Icon, label, value }) => (
  <div className="p-3.5 bg-[#FAFAF5] border border-[#BFA66A] rounded-xl">
    <div className="flex items-center gap-1.5 text-[#5F5F5F] mb-2">
      <Icon className="w-3.5 h-3.5 text-[#B88719]" />
      <span className="text-[9px] font-black uppercase tracking-widest">{label}</span>
    </div>
    <div className="text-base font-bold text-[#111111]">{value}</div>
  </div>
);

export default SandboxQueuePanel;
