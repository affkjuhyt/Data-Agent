import { describe, expect, it } from 'vitest';
import type { EvalCriterion, Rollout } from '../../lib/v1/agentOps';
import { formatMeasure, formatThreshold, gateOf, passes } from './format';

const passRate: EvalCriterion = {
  key: 'pass_rate',
  label: 'Tỷ lệ đạt',
  unit: 'percent',
  baseline: 95,
  candidate: 97.5,
  threshold: { op: 'gte', value: 95 },
};
const regressions: EvalCriterion = {
  key: 'regressions',
  label: 'Câu đổi kết quả so với bản trước',
  unit: 'count',
  baseline: null,
  candidate: 1,
  threshold: null,
};
const rollout = (criteria: EvalCriterion[]) =>
  ({ eval_set: { id: 'e', case_count: 40, criteria } }) as unknown as Rollout;

describe('agent-ops format', () => {
  it('writes measures as the eval table does, and a missing one as a dash', () => {
    expect(formatMeasure(95, 'percent')).toBe('95,0%');
    expect(formatMeasure(97.5, 'percent')).toBe('97,5%');
    expect(formatMeasure(100, 'percent')).toBe('100%');
    expect(formatMeasure(2, 'count')).toBe('2 câu');
    expect(formatMeasure(9200, 'vnd')).toBe('9.200 đ');
    expect(formatMeasure(6, 'minutes')).toBe('6 phút');
    expect(formatMeasure(null, 'percent')).toBe('—');
  });

  it('writes thresholds with their comparison and checks them', () => {
    expect(formatThreshold(passRate)).toBe('≥ 95%');
    expect(formatThreshold(regressions)).toBe('—');
    expect(passes(passRate)).toBe(true);
    expect(passes({ ...passRate, candidate: 94 })).toBe(false);
    expect(passes(regressions)).toBe(true);
  });

  it('opens the gate only when every criterion with a threshold passes', () => {
    expect(gateOf(rollout([passRate, regressions]))).toEqual({ passed: 1, total: 1, open: true });
    expect(gateOf(rollout([{ ...passRate, candidate: 90 }, regressions]))).toEqual({
      passed: 0,
      total: 1,
      open: false,
    });
  });
});
