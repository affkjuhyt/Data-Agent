import { formatMoneyShort, formatNumber } from '../../lib/formatVN';
import type { EvalCriterion, EvalUnit, Rollout } from '../../lib/v1/agentOps';
import type { RunStatus } from '../../lib/v1/types';

type Tone = 'success' | 'warning' | 'error' | 'info' | 'primary' | 'neutral' | 'idle';

/** "97,5%", "100%", "8.400 đ", "6 phút" as in the eval table of 2311:20138. */
export function formatMeasure(value: number, unit: EvalUnit): string {
  if (unit === 'vnd') return formatMoneyShort(value);
  if (unit === 'minutes') return `${formatNumber(value, { maxFractionDigits: 0 })} phút`;
  // Rates under 100 keep one decimal so "95,0%" and "97,5%" line up in the column.
  const digits = value < 100 ? 1 : 0;
  return `${formatNumber(value, { minFractionDigits: digits, maxFractionDigits: digits })}%`;
}

const OP = { gte: '≥ ', lte: '≤ ', eq: '' } as const;

/** "≥ 95%", "100%", "≤ 15.000 đ". Thresholds are round numbers, written without decimals. */
export function formatThreshold({ threshold, unit }: EvalCriterion): string {
  const value =
    unit === 'percent'
      ? `${formatNumber(threshold.value, { maxFractionDigits: 1 })}%`
      : formatMeasure(threshold.value, unit);
  return `${OP[threshold.op]}${value}`;
}

export function passes({ candidate, threshold }: EvalCriterion): boolean {
  if (threshold.op === 'gte') return candidate >= threshold.value;
  if (threshold.op === 'lte') return candidate <= threshold.value;
  return candidate === threshold.value;
}

/** The quality gate the widen dialog quotes: "Cổng chất lượng đã đạt 4/4". */
export function gateOf(rollout: Rollout) {
  const criteria = rollout.eval_set.criteria;
  const passed = criteria.filter(passes).length;
  return { passed, total: criteria.length, open: passed === criteria.length };
}

export function rolloutStatus(r: Rollout): { tone: Tone; label: string } {
  switch (r.status) {
    case 'completed':
      return { tone: 'success', label: `Đã triển khai ${r.candidate_share_pct}%` };
    case 'rolled_back':
      return { tone: 'neutral', label: `Đã lùi về v${r.baseline_version}` };
    default:
      return { tone: 'info', label: `Triển khai ${r.candidate_share_pct}%` };
  }
}

/** Status of one agent's run in the team, in the words of 2311:20297. */
export function taskStatus(status: RunStatus): { tone: Tone; label: string } {
  switch (status) {
    case 'queued':
    case 'planning':
      return { tone: 'idle', label: 'Chờ bắt đầu' };
    case 'running':
      return { tone: 'success', label: 'Đang chạy' };
    case 'waiting_human':
      return { tone: 'warning', label: 'Chờ bạn' };
    case 'paused':
      return { tone: 'neutral', label: 'Tạm dừng' };
    case 'succeeded':
      return { tone: 'success', label: 'Xong' };
    case 'failed':
      return { tone: 'error', label: 'Lỗi' };
    case 'cancelled':
      return { tone: 'neutral', label: 'Đã huỷ' };
  }
}
