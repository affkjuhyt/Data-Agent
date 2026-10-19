import { describe, expect, it } from 'vitest';
import { summaryFromRuns } from './adminPlatform';
import type { AgentDef, AgentRun } from './types';

const now = new Date('2026-10-17T12:00:00Z');

function run(
  partial: Partial<AgentRun> & Pick<AgentRun, 'id' | 'status' | 'created_at'>,
): AgentRun {
  return {
    agent_id: 'agent-1',
    data_space_id: 'space',
    config_version: 1,
    trigger_kind: 'manual',
    trigger_ref: null,
    initiated_by: null,
    parent_run_id: null,
    depth: 0,
    is_test: false,
    as_of: null,
    current_step: null,
    cost: '1000',
    tokens: 0,
    waiting_since: null,
    started_at: partial.created_at,
    finished_at: null,
    stop_reason: null,
    workflow_id: null,
    error: null,
    snapshot_refs: [],
    stop_detail: {},
    narrative: [],
    agent_name: 'Biên lợi nhuận gộp',
    total_steps: null,
    eta_text: null,
    code: '#c61',
    title: 'Biên gộp',
    initiated_label: null,
    trigger_text: null,
    hitl_count: 0,
    ...partial,
  };
}

const agent = {
  id: 'agent-1',
  name: 'Biên lợi nhuận gộp',
  monthly_budget: '2000000',
  spent_this_month: '1860000',
} as AgentDef;

describe('summaryFromRuns', () => {
  it('counts the runs the caller can see when the admin usage report is closed', () => {
    const summary = summaryFromRuns(
      '7d',
      [
        run({ id: 'live', status: 'running', created_at: '2026-10-17T10:00:00Z' }),
        run({
          id: 'wait',
          status: 'waiting_human',
          created_at: '2026-10-16T10:00:00Z',
          hitl_count: 1,
          agent_id: 'agent-1',
        }),
        run({
          id: 'old',
          status: 'succeeded',
          created_at: '2026-10-01T10:00:00Z',
          finished_at: '2026-10-01T10:05:00Z',
          stop_reason: 'completed',
        }),
        run({
          id: 'trial',
          status: 'succeeded',
          created_at: '2026-10-17T09:00:00Z',
          is_test: true,
        }),
      ],
      [agent],
      now,
    );
    expect(summary.runs).toBe(2);
    expect(summary.hitl_requests).toBe(1);
    expect(summary.errors).toBe(0);
    expect(summary.agents).toEqual([
      expect.objectContaining({ name: 'Biên lợi nhuận gộp', runs: 2, hitl_requests: 1 }),
    ]);
    expect(summary.issues[0]?.title).toContain('93%');
    expect(summary.budget_used_ratio).toBeCloseTo(0.93);
  });
});
