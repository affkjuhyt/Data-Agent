import { describe, expect, it } from 'vitest';
import { readAgent } from '../../lib/v1/api';
import { agentsFixture } from '../../mocks/fixtures';
import { agentStatus, sevenDaysText } from './agentStatus';

describe('agent team rows', () => {
  it('gives each agent its most urgent status', () => {
    const status = Object.fromEntries(agentsFixture().map((a) => [a.code, agentStatus(readAgent(a)).label]));
    expect(status).toEqual({
      RECONCILE: 'Đang chạy',
      ANALYSIS: 'Chờ bạn',
      RISK_WATCH: 'Đang chạy',
      DATA_QUALITY: 'Lỗi',
      REPORT: 'Chờ lịch',
    });
  });

  it('summarises the last seven days in words', () => {
    const [reconcile, , , dq] = agentsFixture().map(readAgent);
    expect(sevenDaysText(reconcile)).toBe('12 lượt · 58% cần duyệt');
    expect(sevenDaysText(dq)).toBe('30 lượt · 1 lỗi');
  });
});
