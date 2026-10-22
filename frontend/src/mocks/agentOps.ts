import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';
import type { RunWire } from '../lib/v1/types';

type Schemas = components['schemas'];

/*
 * MSW handlers for the A2 reads behind lib/v1/agentOps.ts. "P13 · Đánh giá & triển khai"
 * (2311:20138): Agent Thu nợ has v6 rolling at 50% against v5, with its config versions, the
 * eval run of v6 and its eval set; the deployment's state lives in memory so widening and
 * rolling back show on the next read. "P13 · Điều phối nhiều agent" (2311:20297): a lead run and
 * its three child runs, merged into the run mock of handlers.ts (teamRunsFixture), which also
 * answers GET /v1/runs/{id}/children and pause/resume.
 *
 * Placed before the agent builder mock: GET /v1/agents/{id} answers the rollout agent only and
 * falls through for every other agent.
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    {
      type: `https://docs.dataagent/errors/${code}`,
      title: code,
      status,
      code,
      detail,
      trace_id: 'mock',
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const DS = 'ds-rui-ro-tin-dung';

export const ROLLOUT_AGENT_ID = 'agent-collect';
const EVAL_SET_ID = 'eval-collect';
const EVAL_RUN_ID = 'er-collect-6';

export function deploymentFixture(): Schemas['DeploymentView'] {
  return {
    id: 'dep-collect-6',
    agent_id: ROLLOUT_AGENT_ID,
    config_version: 6,
    previous_version: 5,
    traffic_pct: 50,
    gates: {
      error_rate_increase_pts: 5,
      needs_human_increase_pts: 10,
      cost_per_run_increase_pct: 30,
      min_runs: 20,
    },
    observed: {
      window_days: 7,
      baseline: { runs: 412, needs_human_pct: 12, errors: 3 },
      candidate: { runs: 398, needs_human_pct: 9, errors: 2 },
    },
    status: 'rolling',
    rollback_reason: null,
    approved_by: 'u-duc-long',
    hitl_request_id: null,
    started_at: minutesAgo(3 * 24 * 60),
    finished_at: null,
    created_at: minutesAgo(3 * 24 * 60),
    updated_at: minutesAgo(60),
  };
}

const config = (version: number, note: string, status: string, evalRunId: string | null) => ({
  id: `config-collect-${version}`,
  agent_id: ROLLOUT_AGENT_ID,
  version,
  goal: 'Nhắc khách hàng sắp quá hạn và báo cáo tỷ lệ thu hồi',
  data_scope: {},
  skill_ids: [],
  autonomy: 'ask_before_send',
  checkpoints: [],
  denied_actions: [],
  limits: {},
  model_by_task: {},
  change_note: note,
  status,
  eval_run_id: evalRunId,
  created_at: minutesAgo(version * 24 * 60),
});

export function evalRunFixture(): Schemas['EvalRunDetail'] {
  return {
    id: EVAL_RUN_ID,
    eval_set_id: EVAL_SET_ID,
    subject_version: '6',
    trigger: 'config',
    trigger_ref: 'config-collect-6',
    item_count: 40,
    passed: 39,
    failed: 1,
    correct_refusals: 2,
    pass_rate: 0.975,
    baseline_run_id: 'er-collect-5',
    regressions: 1,
    status: 'succeeded',
    started_at: minutesAgo(4 * 24 * 60),
    finished_at: minutesAgo(4 * 24 * 60 - 10),
    created_at: minutesAgo(4 * 24 * 60),
    baseline: {
      baseline_run_id: 'er-collect-5',
      passed: 38,
      failed: 2,
      pass_rate: 0.95,
      passed_delta: 1,
    },
    results: [],
  };
}

const evalSet = (itemCount: number): Schemas['EvalSetDetail'] => ({
  id: EVAL_SET_ID,
  data_space_id: DS,
  subject_type: 'agent',
  subject_id: ROLLOUT_AGENT_ID,
  name: 'Bộ thử Agent Thu nợ',
  pass_threshold: 0.95,
  min_items: 30,
  status: 'active',
  item_count: itemCount,
  created_at: minutesAgo(60 * 24 * 60),
  updated_at: minutesAgo(4 * 24 * 60),
});

/* ─── Điều phối: a lead run and its children, in the run mock's shape ─── */

const teamRun = (
  id: string,
  agentId: string,
  status: RunWire['status'],
  cost: number,
  parent: string | null,
): RunWire => ({
  id,
  agent_id: agentId,
  config_version: 5,
  data_space_id: DS,
  trigger_kind: parent ? 'agent_call' : 'event',
  trigger_ref: null,
  initiated_by: null,
  parent_run_id: parent,
  depth: parent ? 1 : 0,
  is_test: false,
  as_of: null,
  status,
  current_step: null,
  cost: String(cost),
  tokens: 0,
  waiting_since: status === 'waiting_human' ? minutesAgo(3) : null,
  started_at: minutesAgo(11),
  finished_at: status === 'succeeded' ? minutesAgo(4) : null,
  stop_reason: status === 'succeeded' ? 'completed' : null,
  created_at: minutesAgo(11),
  narrative: [],
  error: null,
  hitl_count: status === 'waiting_human' ? 1 : 0,
});

const delegation = (seq: number, child: string, name: string, summary: string | null) => ({
  id: `step-4480-${seq}`,
  seq,
  kind: 'agent_call',
  name,
  input: {},
  output: null,
  summary,
  status: summary ? 'succeeded' : 'running',
  attempt: 1,
  child_run_id: child,
  cost: '0',
  started_at: minutesAgo(10),
  duration_ms: null,
});

/** Lead first; its technical view holds the step that handed out each child run. */
export function teamRunsFixture(): RunWire[] {
  const lead = teamRun('run-4480', 'agent-analysis', 'running', 12400, null);
  return [
    {
      ...lead,
      steps: [
        delegation(
          1,
          'run-4481',
          'Kiểm tra dữ liệu DH Bắc Bộ có bất thường không',
          'Không bất thường; DWH bán lẻ trễ 8 giờ nhưng không ảnh hưởng',
        ),
        delegation(
          2,
          'run-4482',
          'So nhóm nợ trước/sau với số đã nộp',
          '19 khoản cơ cấu chuyển nhóm 2 → 3',
        ),
        delegation(3, 'run-4483', 'Theo dõi khách hàng bị chuyển nhóm', null),
      ],
    },
    teamRun('run-4481', 'agent-dq', 'succeeded', 5600, 'run-4480'),
    teamRun('run-4482', 'agent-reconcile', 'succeeded', 8400, 'run-4480'),
    teamRun('run-4483', 'agent-risk', 'waiting_human', 4800, 'run-4480'),
  ];
}

export function createAgentOpsHandlers() {
  let deployment = deploymentFixture();
  let itemCount = 40;

  const agent = (): Schemas['AgentView'] => ({
    id: ROLLOUT_AGENT_ID,
    data_space_id: DS,
    code: 'COLLECT',
    name: 'Agent Thu nợ',
    kind: 'collect',
    owner_user_id: 'u-duc-long',
    account_user_id: 'u-agent',
    template_ref: null,
    current_config_version: deployment.status === 'completed' ? 6 : 5,
    status: 'running',
    paused_reason: null,
    monthly_budget: '2000000',
    spent_this_month: '318000',
    active_runs: 1,
    waiting_runs: 0,
    created_at: minutesAgo(60 * 24 * 60),
    updated_at: minutesAgo(60),
    current_config: null,
    // GET /v1/agents/{id} carries only a deployment still in progress.
    active_deployment: ['pending_approval', 'rolling'].includes(deployment.status)
      ? deployment
      : null,
  });

  return [
    http.get('*/v1/agents/:id', ({ params }) =>
      params.id === ROLLOUT_AGENT_ID ? HttpResponse.json(agent()) : undefined,
    ),
    http.get('*/v1/agents/:id/configs', ({ params }) =>
      params.id === ROLLOUT_AGENT_ID
        ? page([
            config(6, 'v6 thêm trigger theo job nạp và điều kiện dừng chi phí', 'passed', EVAL_RUN_ID),
            config(5, 'v5 nhắc qua Zalo trước email', 'deployed', 'er-collect-5'),
          ])
        : undefined,
    ),
    http.get('*/v1/eval-runs/:id', ({ params }) =>
      params.id === EVAL_RUN_ID ? HttpResponse.json(evalRunFixture()) : undefined,
    ),
    http.get('*/v1/eval-sets/:id', ({ params }) =>
      params.id === EVAL_SET_ID ? HttpResponse.json(evalSet(itemCount)) : undefined,
    ),
    http.post('*/v1/eval-sets/:id/items', async ({ params, request }) => {
      if (params.id !== EVAL_SET_ID) return undefined;
      const body = (await request.json()) as Schemas['EvalItemCreate'];
      const text = (v: unknown) => (v as { text?: string } | null)?.text?.trim();
      if (!text(body.input) || !text(body.expected))
        return problem(422, 'validation_failed', 'Cần nhập đầu vào và kỳ vọng.');
      itemCount += 1;
      const now = new Date().toISOString();
      return HttpResponse.json(
        {
          id: `item-${itemCount}`,
          eval_set_id: EVAL_SET_ID,
          category: body.category ?? 'correct_result',
          input: body.input,
          as_of: null,
          expected: body.expected,
          tolerance: null,
          origin: 'manual',
          origin_ref: null,
          status: 'active',
          created_at: now,
          updated_at: now,
        } satisfies Schemas['EvalItemView'],
        { status: 201 },
      );
    }),
    http.patch('*/v1/deployments/:id', async ({ params, request }) => {
      if (params.id !== deployment.id) return undefined;
      if (deployment.status !== 'rolling')
        return problem(409, 'invalid_state', `A deployment in status ${deployment.status} takes no traffic change.`);
      const { traffic_pct: pct } = (await request.json()) as Schemas['DeploymentPatch'];
      if (!Number.isInteger(pct) || pct < 0 || pct > 100)
        return problem(422, 'validation_failed', 'Tỷ lệ phải từ 0 đến 100.');
      deployment = {
        ...deployment,
        traffic_pct: pct,
        status: pct === 100 ? 'completed' : 'rolling',
        finished_at: pct === 100 ? new Date().toISOString() : null,
        updated_at: new Date().toISOString(),
      };
      return HttpResponse.json(deployment);
    }),
    http.post('*/v1/deployments/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== deployment.id) return undefined;
      if (verb !== 'rollback') return problem(404, 'not_found', 'Không có thao tác này.');
      if (!['rolling', 'completed'].includes(deployment.status))
        return problem(409, 'invalid_state', 'Triển khai này không quay lui được.');
      const { reason } = (await request.json()) as Schemas['DeploymentRollback'];
      if (!reason?.trim()) return problem(422, 'validation_failed', 'Cần ghi lý do quay lui.');
      deployment = {
        ...deployment,
        status: 'rolled_back',
        rollback_reason: reason,
        finished_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      return HttpResponse.json(deployment);
    }),
  ];
}
