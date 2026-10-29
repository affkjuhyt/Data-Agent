/**
 * "Kích hoạt" deploys only a version that passed its eval set (UC-13-04 before UC-13-05): a version
 * not yet passed is evaluated first, and one below the bar, or without a set, is not deployed.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { activateAgent } from './agentBuilder';

const at = '2026-10-29T08:00:00Z';
const config = (status: string) => ({
  id: 'cfg-1',
  agent_id: 'a-1',
  version: 1,
  goal: 'g',
  data_scope: {},
  skill_ids: [],
  autonomy: 'propose_approve',
  checkpoints: [],
  denied_actions: [],
  limits: {},
  model_by_task: {},
  change_note: null,
  status,
  eval_run_id: status === 'draft' ? null : 'er-1',
  created_at: at,
  tools: [],
});
const run = (passed: number) => ({
  id: 'er-1',
  eval_set_id: 'set-1',
  subject_version: 'v1',
  trigger: 'deploy',
  trigger_ref: 'cfg-1',
  item_count: 120,
  passed,
  failed: 120 - passed,
  correct_refusals: 0,
  pass_rate: passed / 120,
  baseline_run_id: null,
  baseline: null,
  regressions: 0,
  status: 'completed',
  started_at: at,
  finished_at: at,
  created_at: at,
  results: [],
});
const spaceSet = {
  id: 'set-1',
  data_space_id: 'ds-1',
  subject_type: 'context',
  subject_id: null,
  name: 'Bộ thử',
  pass_threshold: 0.95,
  min_items: 100,
  status: 'active',
  created_at: at,
  updated_at: at,
  item_count: 120,
};
const page = (items: unknown[]) => HttpResponse.json({ items, next_page_token: null });

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** The api as the eval worker leaves it: the run 404 until it starts, then scored. */
function evalWorker({ passes, sets = [spaceSet] }: { passes: boolean; sets?: unknown[] }) {
  let status = 'draft';
  let polls = 0;
  const deployed: number[] = [];
  server.use(
    http.get('*/v1/agents/a-1/configs', () => page([config(status)])),
    http.get('*/v1/eval-sets', () => page(sets)),
    http.post('*/v1/agents/a-1/eval-runs', () => {
      status = 'testing';
      return HttpResponse.json({ id: 'er-1', status: 'queued', poll_after_ms: 1 }, { status: 202 });
    }),
    http.get('*/v1/eval-runs/er-1', () => {
      polls += 1;
      if (polls === 1)
        return HttpResponse.json({ code: 'not_found', detail: 'Eval run not found.' }, { status: 404 });
      if (passes) status = 'passed';
      return HttpResponse.json(run(passes ? 118 : 90));
    }),
    http.post('*/v1/agents/a-1/deployments', async ({ request }) => {
      deployed.push(((await request.json()) as { config_version: number }).config_version);
      return HttpResponse.json(
        { id: 'dep-1', agent_id: 'a-1', config_version: 1, traffic_pct: 100, status: 'pending_approval' },
        { status: 201 },
      );
    }),
  );
  return { deployed };
}

const instant = () => Promise.resolve();

describe('activating an agent version', () => {
  it('evaluates a version first and deploys it once it passed', async () => {
    const api = evalWorker({ passes: true });
    await activateAgent('a-1', 1, instant);
    expect(api.deployed).toEqual([1]);
  });

  it('does not deploy a version below the bar, and says how it scored', async () => {
    const api = evalWorker({ passes: false });
    await expect(activateAgent('a-1', 1, instant)).rejects.toThrow(
      'Phiên bản 1 chưa đạt bộ đánh giá: 90/120 tình huống đạt.',
    );
    expect(api.deployed).toEqual([]);
  });

  it('says a Data Space without an eval set must get one, without asking for a run', async () => {
    const api = evalWorker({ passes: true, sets: [{ ...spaceSet, data_space_id: null, subject_type: 'pack' }] });
    await expect(activateAgent('a-1', 1, instant)).rejects.toThrow(/chưa có bộ đánh giá/);
    expect(api.deployed).toEqual([]);
  });
});
