import { isLocked } from '../../lib/v1/agentControl';
import type { AgentDef } from '../../lib/v1/types';

/*
 * One status per agent row ("Trạng thái" on "P13 · Đội agent"), most urgent first:
 * locked by its owner, an error, then waiting on a person, paused, running, and otherwise
 * waiting for its schedule.
 */
type Tone = 'error' | 'warning' | 'neutral' | 'success' | 'idle';

export function agentStatus(agent: AgentDef): { label: string; tone: Tone } {
  if (isLocked(agent)) return { label: 'Đã khoá', tone: 'neutral' };
  if (agent.now?.tone === 'error') return { label: 'Lỗi', tone: 'error' };
  if (agent.now?.tone === 'waiting' || agent.waiting_runs > 0)
    return { label: 'Chờ bạn', tone: 'warning' };
  if (agent.status === 'paused') return { label: 'Tạm dừng', tone: 'neutral' };
  if (agent.active_runs > 0) return { label: 'Đang chạy', tone: 'success' };
  return { label: 'Chờ lịch', tone: 'idle' };
}

/**
 * "Đang làm gì": the PROPOSAL sentence when the api sends it, otherwise what the run counts and
 * the pause reason say.
 */
export function nowOf(agent: AgentDef): NonNullable<AgentDef['now']> {
  if (agent.now) return agent.now;
  if (agent.waiting_runs > 0)
    return { text: `${agent.waiting_runs} lượt chờ người quyết`, tone: 'waiting' };
  if (agent.active_runs > 0) return { text: `${agent.active_runs} lượt đang chạy`, tone: 'normal' };
  if (agent.status === 'paused')
    return { text: agent.paused_reason ? `Tạm dừng: ${agent.paused_reason}` : 'Tạm dừng', tone: 'normal' };
  return { text: 'Không có lượt nào đang chạy', tone: 'normal' };
}

/** "12 lượt · 58% cần duyệt"; empty without the PROPOSAL `runs_7d`. */
export function sevenDaysText(agent: AgentDef): string {
  if (!agent.runs_7d) return '';
  const { count, needs_approval_pct, errors } = agent.runs_7d;
  return [
    `${count} lượt`,
    needs_approval_pct > 0 ? `${needs_approval_pct}% cần duyệt` : null,
    errors > 0 ? `${errors} lỗi` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}
