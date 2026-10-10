import { describe, expect, it } from 'vitest';
import { rolloutsFixture } from '../../mocks/agentOps';
import { formatMeasure, formatThreshold, gateOf, passes } from './format';

describe('agent-ops format', () => {
  it('writes measures as the eval table does', () => {
    expect(formatMeasure(95, 'percent')).toBe('95,0%');
    expect(formatMeasure(97.5, 'percent')).toBe('97,5%');
    expect(formatMeasure(100, 'percent')).toBe('100%');
    expect(formatMeasure(9200, 'vnd')).toBe('9.200 đ');
    expect(formatMeasure(6, 'minutes')).toBe('6 phút');
  });

  it('writes thresholds with their comparison and checks them', () => {
    const [{ eval_set }] = rolloutsFixture();
    expect(eval_set.criteria.map(formatThreshold)).toEqual([
      '≥ 95%',
      '100%',
      '100%',
      '≤ 15.000 đ',
      '≤ 20 phút',
    ]);
    expect(eval_set.criteria.every(passes)).toBe(true);
    expect(passes({ ...eval_set.criteria[3], candidate: 15001 })).toBe(false);
  });

  it('opens the gate only when every criterion passes', () => {
    const [rollout] = rolloutsFixture();
    expect(gateOf(rollout)).toEqual({ passed: 5, total: 5, open: true });
    rollout.eval_set.criteria[1].candidate = 97.5;
    expect(gateOf(rollout)).toEqual({ passed: 4, total: 5, open: false });
  });
});
