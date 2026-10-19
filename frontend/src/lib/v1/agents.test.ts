/**
 * Agents and runs as the api sends them (schema.ts), without the PROPOSAL display fields: the
 * readers join, derive or leave empty what the screens draw.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { nowOf, sevenDaysText } from '../../features/agents/agentStatus';
import { addMemory, draftAgent, getAgent } from './agentBuilder';
import { getRun, listAgents, listRuns } from './api';

const at = '2026-10-12T08:00:00Z';
const agent = {
  id: 'a-1',
  data_space_id: 'ds',
  account_user_id: 'u-a',
  owner_user_id: 'u-o',
  code: 'RECONCILE',
  name: 'Agent Đối soát',
  kind: 'reconcile',
  status: 'running',
  paused_reason: null,
  monthly_budget: null,
  spent_this_month: '0',
  current_config_version: null,
  template_ref: null,
  active_runs: 0,
  waiting_runs: 2,
  created_at: at,
  updated_at: at,
};
const run = {
  id: '0192f0aa-1111-7000-8000-000000000001',
  agent_id: 'a-1',
  data_space_id: 'ds',
  config_version: 1,
  status: 'waiting_human',
  trigger_kind: 'manual',
  trigger_ref: null,
  current_step: 2,
  cost: 1200,
  tokens: 0,
  depth: 0,
  is_test: false,
  parent_run_id: null,
  initiated_by: null,
  as_of: null,
  waiting_since: at,
  started_at: at,
  finished_at: null,
  stop_reason: null,
  created_at: at,
};

const server = setupServer(
  http.get('*/v1/agents', () => HttpResponse.json({ items: [agent], next_page_token: '' })),
  http.get('*/v1/agents/:id', () => HttpResponse.json({ ...agent, current_config: null })),
  http.get('*/v1/runs', () => HttpResponse.json({ items: [run], next_page_token: '' })),
  http.get('*/v1/runs/:id', () =>
    HttpResponse.json({
      ...run,
      error: null,
      workflow_id: null,
      snapshot_refs: null,
      stop_detail: null,
      narrative: [
        { event: 'plan.edited', plan_version: 2, at },
        { event: 'step.started' },
        { at, text: 'Đang so dư nợ' },
      ],
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('agents and runs from the api', () => {
  it('says what an agent does from its run counts', async () => {
    const [a] = (await listAgents()).items;
    expect(nowOf(a)).toEqual({ text: '2 lượt chờ người quyết', tone: 'waiting' });
    expect(sevenDaysText(a)).toBe('');
    expect(a.goal).toBeNull();
  });

  it('names a run after its agent and gives it a short code', async () => {
    const [r] = (await listRuns()).items;
    expect(r.agent_name).toBe('Agent Đối soát');
    expect(r.title).toBe('Lượt chạy của Agent Đối soát');
    expect(r.code).toBe('#0192f0aa');
  });

  it('keeps a goal and builds the plan from the steps the run recorded', async () => {
    server.use(
      http.get('*/v1/agents', () =>
        HttpResponse.json({
          items: [
            {
              ...agent,
              goal: 'So số nội bộ',
              trigger_text: 'Theo lịch 07:00 thứ Hai',
              runs_7d: { count: 4, needs_approval_pct: 25, errors: 1 },
              active_deployment_id: 'dep-1',
            },
          ],
          next_page_token: null,
        }),
      ),
      http.get('*/v1/runs/:id', () =>
        HttpResponse.json({
          ...run,
          title: 'So số nội bộ',
          hitl_count: 1,
          steps: [
            {
              id: 's1',
              seq: 1,
              kind: 'query',
              name: 'query.semantic',
              summary: 'Doanh thu tháng 9',
              status: 'ok',
              attempt: 1,
              cost: '1',
              started_at: at,
              duration_ms: 800,
              input: { sql: 'select 1' },
              output: null,
              child_run_id: null,
            },
          ],
        }),
      ),
    );
    const [a] = (await listAgents()).items;
    expect(a.goal).toBe('So số nội bộ');
    expect(sevenDaysText(a)).toBe('4 lượt · 25% cần duyệt · 1 lỗi');
    expect(a.active_deployment_id).toBe('dep-1');
    const detail = await getRun(run.id, 'technical');
    expect(detail.title).toBe('So số nội bộ');
    expect(detail.plan?.[0]).toMatchObject({ title: 'Doanh thu tháng 9', state: 'done' });
  });

  it('keeps the narrative entries that read as a sentence', async () => {
    const r = await getRun(run.id);
    expect(r.agent_name).toBe('Agent Đối soát');
    expect(r.narrative.map((n) => n.text)).toEqual(['Kế hoạch được sửa (bản 2)', 'Đang so dư nợ']);
  });

  it('reads an agent without a config version', async () => {
    const detail = await getAgent('a-1');
    expect(detail.config).toBeNull();
    expect(detail.triggers).toEqual([]);
    expect(detail.owner).toEqual({ id: 'u-o', name: '' });
  });

  it('builds the draft lines and the warning from a ConfigDraft', async () => {
    server.use(
      http.post('*/v1/agents:draft-from-text', () =>
        HttpResponse.json({
          goal: 'So số nội bộ với báo cáo đã nộp',
          autonomy: 'propose_approve',
          checkpoints: ['Lệch vượt ngưỡng trọng yếu'],
          data_scope: { metrics: ['NPL_RATIO'] },
          denied_actions: [],
          limits: {},
          model_by_task: {},
          skill_ids: [],
          template_ref: null,
          tools: [{ tool: 'query', permission_level: 'read' }],
          out_of_scope: ['Core banking'],
        }),
      ),
    );
    const draft = await draftAgent({ description: 'Đối soát dư nợ hằng tháng. Gửi cho tôi.' });
    expect(draft.name).toBe('Đối soát dư nợ hằng tháng');
    expect(draft.code).toBe('DOI_SOAT_DU_NO_HANG_THANG');
    expect(draft.lines.map((l) => [l.field, l.text])).toEqual([
      ['goal', 'So số nội bộ với báo cáo đã nộp'],
      ['data', 'metrics: NPL_RATIO'],
      ['allowed', 'query (đọc)'],
      ['checkpoints', 'Lệch vượt ngưỡng trọng yếu'],
    ]);
    expect(draft.warning).toBe('Ngoài quyền của bạn: Core banking.');
  });

  it('sends a memory expiry as the date the api stores', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/v1/agents/:id/memories', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            id: 'm-1',
            agent_id: 'a-1',
            data_space_id: 'ds',
            scope: 'agent',
            user_id: null,
            kind: 'preference',
            content: 'Tách khoản cơ cấu',
            source_kind: 'manual',
            source_ref: null,
            status: 'active',
            use_count: 0,
            correction_count: 0,
            expires_at: null,
            created_at: at,
            updated_at: at,
          },
          { status: 201 },
        );
      }),
    );
    const memory = await addMemory('a-1', {
      scope: 'agent',
      kind: 'preference',
      content: 'Tách khoản cơ cấu',
      expiry: 'never',
    });
    expect(body).toMatchObject({ expires_at: null });
    expect(body).not.toHaveProperty('expiry');
    expect(memory.expiry).toBe('never');
    expect(memory.scope_text).toBe('Riêng agent này');
  });
});
