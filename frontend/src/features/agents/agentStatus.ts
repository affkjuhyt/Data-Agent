import type { AgentDef } from '../../lib/v1/types';

/*
 * One status per agent row ("Trạng thái" on "P13 · Đội agent"), most urgent first:
 * an error, then waiting on a person, paused, running, and otherwise waiting for its schedule.
 */
type Tone = 'error' | 'warning' | 'neutral' | 'success' | 'idle';

export function agentStatus(agent: AgentDef): { label: string; tone: Tone } {
  if (agent.now.tone === 'error') return { label: 'Lỗi', tone: 'error' };
  if (agent.now.tone === 'waiting' || agent.runs_waiting_human > 0)
    return { label: 'Chờ bạn', tone: 'warning' };
  if (agent.status === 'paused') return { label: 'Tạm dừng', tone: 'neutral' };
  if (agent.runs_running > 0) return { label: 'Đang chạy', tone: 'success' };
  return { label: 'Chờ lịch', tone: 'idle' };
}

export function sevenDaysText(agent: AgentDef): string {
  const { count, needs_approval_pct, errors } = agent.runs_7d;
  return [
    `${count} lượt`,
    needs_approval_pct > 0 ? `${needs_approval_pct}% cần duyệt` : null,
    errors > 0 ? `${errors} lỗi` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}
