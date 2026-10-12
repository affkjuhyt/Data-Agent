import type { AgentStatus } from '../../design-system/AgentChip';
import type { AgentRun, RunPlanStep, RunStatus } from '../../lib/v1/types';

/*
 * How a run reads in words (spec "Quy tắc viết": agent as subject, statuses in plain words):
 * the dot and label of the "Trạng thái" column, and the agent's chip state while it runs.
 */
type Tone = 'info' | 'warning' | 'success' | 'error' | 'neutral' | 'idle';

const STATUS: Record<RunStatus, { label: string; tone: Tone; chip: AgentStatus }> = {
  queued: { label: 'Đang xếp hàng', tone: 'idle', chip: 'idle' },
  planning: { label: 'Đang lập kế hoạch', tone: 'info', chip: 'running' },
  running: { label: 'Đang chạy', tone: 'info', chip: 'running' },
  waiting_human: { label: 'Chờ bạn', tone: 'warning', chip: 'waiting' },
  paused: { label: 'Tạm dừng', tone: 'neutral', chip: 'paused' },
  succeeded: { label: 'Hoàn tất', tone: 'success', chip: 'idle' },
  failed: { label: 'Lỗi', tone: 'error', chip: 'error' },
  cancelled: { label: 'Đã huỷ', tone: 'neutral', chip: 'idle' },
};

export function runStatus(run: AgentRun) {
  const s = STATUS[run.status];
  // A failure names its cause when the run gives one ("Lỗi kết nối").
  return run.status === 'failed' && run.error ? { ...s, label: run.error } : s;
}

export type RunFilter = 'all' | 'active' | 'waiting' | 'failed' | 'done';

export function matchesFilter(run: AgentRun, filter: RunFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'active') return ['queued', 'planning', 'running', 'paused'].includes(run.status);
  if (filter === 'waiting') return run.status === 'waiting_human';
  if (filter === 'failed') return run.status === 'failed';
  return run.status === 'succeeded' || run.status === 'cancelled';
}

/** "1:12": minutes:seconds since it started (or until it finished). */
export function runDuration(run: AgentRun, now = Date.now()): string {
  if (!run.started_at) return '—';
  const end = run.finished_at ? new Date(run.finished_at).getTime() : now;
  const secs = Math.max(0, Math.round((end - new Date(run.started_at).getTime()) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

/** "8k": the short cost of the table; costs are decimals in đồng (a string in JSON). */
export function shortCost(cost: number | string): string {
  const n = Number(cost);
  if (!Number.isFinite(n)) return '—';
  return n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`;
}

export const PLAN_STEP: Record<RunPlanStep['state'], { label: string; tone: Tone }> = {
  done: { label: 'Xong', tone: 'success' },
  running: { label: 'Đang làm', tone: 'info' },
  pending_approval: { label: 'Chờ duyệt', tone: 'warning' },
  error: { label: 'Lỗi', tone: 'error' },
  waiting: { label: 'Chưa', tone: 'idle' },
};
