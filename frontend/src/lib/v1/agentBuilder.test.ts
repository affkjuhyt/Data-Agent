/**
 * The test run of an agent (UC-13-04) as the api sends it: RunView, whose steps (view=technical)
 * tell what the run did, what it was refused and what it would have sent.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getTestRun, readTrigger, saveTriggers } from './agentBuilder';

const at = '2026-11-19T08:00:00Z';
const step = (seq: number, kind: string, name: string, status: string, summary: string) => ({
  id: `s-${seq}`,
  seq,
  kind,
  name,
  status,
  summary,
  attempt: 1,
  child_run_id: null,
  cost: '0',
  duration_ms: 1200,
  input: null,
  output: null,
  started_at: at,
});

const run = (over: Record<string, unknown>) => ({
  id: 'run-1',
  agent_id: 'agent-1',
  config_version: 2,
  status: 'succeeded',
  cost: '9200',
  is_test: true,
  hitl_count: 1,
  error: null,
  as_of: '2026-10-05',
  started_at: at,
  finished_at: '2026-11-19T08:00:42Z',
  steps: [
    step(1, 'query', 'query.semantic', 'ok', 'Tính NPL theo vùng tuần 40'),
    step(2, 'tool_call', 'quality.status', 'skipped', 'Agent chưa được cấp công cụ kiểm tra chất lượng.'),
    step(3, 'tool_call', 'notify.owner', 'simulated', 'Sẽ gửi kết quả tới chủ sở hữu agent; chưa gửi.'),
  ],
  ...over,
});

let view: string | null = null;
let answer: Record<string, unknown> = run({});
const server = setupServer(
  http.get('*/v1/runs/run-1', ({ request }) => {
    view = new URL(request.url).searchParams.get('view');
    return HttpResponse.json(answer);
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('the test run drawer from the api', () => {
  it('reports an ended run from its own steps, with no test suite it did not run', async () => {
    answer = run({});
    const got = await getTestRun('run-1', 'Agent Đối soát');
    expect(view).toBe('technical');
    expect(got.agent_name).toBe('Agent Đối soát');
    expect(got.test_steps).toEqual([
      { title: 'Tính NPL theo vùng tuần 40', detail: 'query.semantic · 1,2s', state: 'done' },
      {
        title: 'Agent chưa được cấp công cụ kiểm tra chất lượng.',
        detail: 'quality.status · 1,2s',
        state: 'error',
      },
      {
        title: 'Sẽ gửi kết quả tới chủ sở hữu agent; chưa gửi.',
        detail: 'notify.owner · 1,2s',
        state: 'done',
      },
    ]);
    expect(got.test_report).toEqual({
      completed: true,
      human_questions: 1,
      policy_violations: 1,
      eval_passed: null,
      eval_total: null,
      data_text: 'Trên dữ liệu ngày 05/10/2026',
      preview: {
        label: 'Sẽ gửi (chưa gửi)',
        text: 'Sẽ gửi kết quả tới chủ sở hữu agent; chưa gửi.',
      },
      failures: ['Agent chưa được cấp công cụ kiểm tra chất lượng.'],
    });
  });

  it('has no report while the run goes on', async () => {
    answer = run({ status: 'running', finished_at: null });
    expect((await getTestRun('run-1', 'Agent Đối soát')).test_report).toBeNull();
  });

  it('says why a failed run stopped', async () => {
    answer = run({ status: 'failed', error: 'Không kết nối được DWH.', steps: [] });
    expect((await getTestRun('run-1', 'Agent Đối soát')).test_report).toMatchObject({
      completed: false,
      failures: ['Không kết nối được DWH.'],
    });
  });
});

describe('saving an agent’s triggers', () => {
  it('sends every trigger whole, so an edit never drops what it did not show', async () => {
    let sent: unknown;
    server.use(
      http.put('*/v1/agents/agent-1/triggers', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ items: [], next_page_token: '' });
      }),
    );
    const schedule = readTrigger({
      id: 't-1',
      agent_id: 'agent-1',
      kind: 'schedule',
      cron: '0 7 * * 1',
      work_calendar_id: 'cal-1',
      event_type: null,
      condition: {},
      min_interval: 'PT1H',
      no_overlap: false,
      combine: 'all',
      enabled: true,
      created_at: at,
      updated_at: at,
    });
    const event = readTrigger({
      id: 't-2',
      agent_id: 'agent-1',
      kind: 'event',
      cron: null,
      work_calendar_id: null,
      event_type: 'period.closed',
      condition: { space: 'DS-AW' },
      min_interval: null,
      no_overlap: true,
      combine: 'any',
      enabled: true,
      created_at: at,
      updated_at: at,
    });
    await saveTriggers('agent-1', [{ ...schedule, cron: '0 8 * * 1' }, { ...event, enabled: false }]);
    expect(sent).toEqual({
      triggers: [
        {
          kind: 'schedule',
          cron: '0 8 * * 1',
          work_calendar_id: 'cal-1',
          event_type: null,
          condition: {},
          min_interval: 'PT1H',
          no_overlap: false,
          combine: 'all',
          enabled: true,
        },
        {
          kind: 'event',
          cron: null,
          work_calendar_id: null,
          event_type: 'period.closed',
          condition: { space: 'DS-AW' },
          min_interval: null,
          no_overlap: true,
          combine: 'any',
          enabled: false,
        },
      ],
    });
  });
});
