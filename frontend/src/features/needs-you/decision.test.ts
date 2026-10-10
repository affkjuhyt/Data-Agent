import { describe, expect, it } from 'vitest';
import type { HitlRequest } from '../../lib/v1/types';
import { inboxFixture } from '../../mocks/fixtures';
import { decisionKindOf, dueOf } from './decision';

const byId = (id: string) => inboxFixture().find((r) => r.id === id) as HitlRequest;

describe('decision cards', () => {
  it('draws each HITL kind as the card of the spec', () => {
    expect(decisionKindOf(byId('hitl-npl-definition'))).toBe('choose');
    expect(decisionKindOf(byId('hitl-adjust-214'))).toBe('approve');
    expect(decisionKindOf(byId('hitl-core-vs-dwh'))).toBe('compare');
    expect(decisionKindOf(byId('hitl-report-sept'))).toBe('review');
  });

  it('answers a clarification without choices in words', () => {
    expect(decisionKindOf({ ...byId('hitl-npl-definition'), options: [] })).toBe('approve');
  });

  it('warns under four hours, then gives the hour', () => {
    const now = new Date(2026, 9, 8, 9, 0);
    const at = (h: number, m = 0) => ({
      ...byId('hitl-adjust-214'),
      due_at: new Date(2026, 9, 8, h, m).toISOString(),
    });
    expect(dueOf(at(10, 4), now)).toEqual({ text: 'còn 1 giờ 04 phút', urgent: true });
    expect(dueOf(at(17), now)).toEqual({ text: 'hạn 17:00 hôm nay', urgent: false });
    expect(dueOf(at(8), now)).toEqual({ text: 'đã quá hạn', urgent: true });
    expect(dueOf({ ...at(9), due_at: new Date(2026, 9, 10, 9, 30).toISOString() }, now)).toEqual({
      text: 'hạn 10/10 09:30',
      urgent: false,
    });
  });
});
