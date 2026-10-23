import { controlRun, listAgents } from './api';
import { type Page, api, ok } from './client';
import type { components } from './schema';
import type { RunStatus } from './types';

type Schemas = components['schemas'];

/*
 * Agent operations of P13: trying a new config version against the eval set and in parallel on
 * live traffic, then widening or rolling it back (UC-13-04, UC-13-05), and a team of agents
 * working one problem under a lead agent (UC-13-08).
 *
 * A2 has no rollout nor orchestration resource. A rollout is the agent's active deployment
 * (GET /v1/agents/{id}: active_deployment, PATCH /v1/deployments/{id} {traffic_pct},
 * POST /v1/deployments/{id}:rollback {reason}), compared on the candidate config's eval run
 * (GET /v1/agents/{id}/configs → eval_run_id, GET /v1/eval-runs/{id}, GET /v1/eval-sets/{id}).
 * A team is a lead run and its child runs (GET /v1/runs/{id}, GET /v1/runs/{id}/children),
 * paused and resumed on the lead (POST /v1/runs/{id}:pause | :resume).
 */
type Uuid = string;

/* ─── Đánh giá & triển khai (UC-13-04, UC-13-05) ─────────────────────── */

/** How a criterion is measured, so the screen formats it ("97,5%", "2 câu"). */
export type EvalUnit = 'percent' | 'count' | 'vnd' | 'minutes';

/** One row of "Bộ đánh giá": the baseline and the candidate on the same eval set. */
export interface EvalCriterion {
  key: string;
  label: string;
  unit: EvalUnit;
  /** Null when the eval run has no baseline to compare with. */
  baseline: number | null;
  candidate: number;
  /** Null for a measure the quality gate does not judge. */
  threshold: { op: 'gte' | 'lte' | 'eq'; value: number } | null;
}

/** One version's numbers on live traffic. */
export interface LiveStats {
  runs: number | null;
  needs_approval_pct: number | null;
  errors: number | null;
}

/** agent_deployment.status. */
export type RolloutStatus =
  | 'pending_approval'
  | 'rolling'
  | 'completed'
  | 'rolled_back'
  | 'cancelled';

/** The version being tried and the one it may replace. */
export interface Rollout {
  agent_id: Uuid;
  agent_name: string;
  deployment_id: Uuid;
  /** Null for an agent's first version. */
  baseline_version: number | null;
  candidate_version: number;
  /** Share of new runs the candidate takes, 0–100. */
  candidate_share_pct: number;
  /** The next step the owner can widen to (10 → 50 → 100); null once at 100. */
  next_share_pct: number | null;
  status: RolloutStatus;
  /** What the candidate changes, from its config version's change note. */
  change_summary: string | null;
  /** The candidate's eval run on its eval set; null before it ran. */
  eval_set: { id: Uuid; case_count: number; criteria: EvalCriterion[] } | null;
  /** deployment.observed; null until the observation window has numbers. */
  live: { window_days: number | null; baseline: LiveStats; candidate: LiveStats } | null;
  /** Automatic rollback conditions (BR-148 gates), in words ("Tự quay lui khi"). */
  auto_rollback_rules: string[];
}

/** UC-13-05: traffic widens 10 → 50 → 100 (agent_deployment.traffic_pct). */
const SHARE_STEPS = [10, 50, 100];
const nextShare = (pct: number) => SHARE_STEPS.find((s) => s > pct) ?? null;

const num = (v: unknown): number | null => {
  const n = typeof v === 'string' ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
};
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** pass_rate is 0–1 or 0–100 depending on the writer; the screen shows percent. */
const percent = (v: unknown) => {
  const n = num(v);
  return n === null ? null : n <= 1 ? n * 100 : n;
};

/** BR-148 gates (agent/rules.py DEFAULT_GATES) in words; another key is shown as written. */
const GATE_TEXT: Record<string, (v: unknown) => string> = {
  error_rate_increase_pts: (v) => `Tỷ lệ lỗi tăng quá ${String(v)} điểm so với bản cũ`,
  needs_human_increase_pts: (v) => `Tỷ lệ cần người duyệt tăng quá ${String(v)} điểm`,
  cost_per_run_increase_pct: (v) => `Chi phí mỗi lượt tăng quá ${String(v)}%`,
  min_runs: (v) => `Xét sau ít nhất ${String(v)} lượt chạy`,
};

const gateRules = (gates: unknown) =>
  isRecord(gates)
    ? Object.entries(gates).map(([k, v]) => GATE_TEXT[k]?.(v) ?? `${k}: ${String(v)}`)
    : [];

const liveStats = (v: unknown): LiveStats => {
  const r = isRecord(v) ? v : {};
  return {
    runs: num(r.runs),
    needs_approval_pct: num(r.needs_human_pct ?? r.needs_approval_pct),
    errors: num(r.errors ?? r.error_count),
  };
};

/*
 * deployment.observed is "Số đo trong cửa sổ quan sát" (jsonb) with no fixed shape yet; it is read
 * as {window_days, baseline: {runs, needs_human_pct, errors}, candidate: {…}} and left out otherwise.
 */
function readLive(observed: unknown): Rollout['live'] {
  if (!isRecord(observed) || !isRecord(observed.candidate)) return null;
  return {
    window_days: num(observed.window_days),
    baseline: liveStats(observed.baseline),
    candidate: liveStats(observed.candidate),
  };
}

type Deployment = Schemas['DeploymentView'];

const withDeployment = (r: Rollout, d: Deployment): Rollout => ({
  ...r,
  deployment_id: d.id,
  baseline_version: d.previous_version,
  candidate_version: d.config_version,
  candidate_share_pct: d.traffic_pct,
  next_share_pct: d.status === 'rolling' ? nextShare(d.traffic_pct) : null,
  status: d.status as RolloutStatus,
  live: readLive(d.observed) ?? r.live,
  auto_rollback_rules: gateRules(d.gates),
});

async function evalOf(run: Schemas['EvalRunDetail']): Promise<NonNullable<Rollout['eval_set']>> {
  const set = await ok<Schemas['EvalSetDetail']>(
    api.GET('/v1/eval-sets/{id}', { params: { path: { id: run.eval_set_id } } }),
  ).catch(() => null);
  const threshold = percent(set?.pass_threshold);
  return {
    id: run.eval_set_id,
    case_count: set?.item_count ?? run.item_count,
    criteria: [
      {
        key: 'pass_rate',
        label: 'Tỷ lệ đạt',
        unit: 'percent',
        baseline: percent(run.baseline?.pass_rate),
        candidate: percent(run.pass_rate) ?? 0,
        threshold: threshold === null ? null : { op: 'gte', value: threshold },
      },
      {
        key: 'regressions',
        label: 'Câu đổi kết quả so với bản trước',
        unit: 'count',
        baseline: null,
        candidate: run.regressions,
        threshold: null,
      },
    ],
  };
}

/** The agent's deployment in progress; null when it has none (UC-13-05 not started or done). */
export const getRollout = async (agentId: string): Promise<Rollout | null> => {
  const agent = await ok<Schemas['AgentView']>(
    api.GET('/v1/agents/{id}', { params: { path: { id: agentId } } }),
  );
  const deployment = agent.active_deployment;
  if (!deployment) return null;
  const configs = await ok<Page<Schemas['ConfigView']>>(
    api.GET('/v1/agents/{id}/configs', {
      params: {
        path: { id: agent.id },
        query: {
          page_size: 500,
        },
      },
    }),
  );
  const candidate = configs.items.find((c) => c.version === deployment.config_version);
  const run = candidate?.eval_run_id
    ? await ok<Schemas['EvalRunDetail']>(
        api.GET('/v1/eval-runs/{id}', { params: { path: { id: candidate.eval_run_id } } }),
      )
    : null;
  const base: Rollout = {
    agent_id: agent.id,
    agent_name: agent.name,
    deployment_id: deployment.id,
    baseline_version: null,
    candidate_version: deployment.config_version,
    candidate_share_pct: 0,
    next_share_pct: null,
    status: 'rolling',
    change_summary: candidate?.change_note ?? null,
    eval_set: run ? await evalOf(run) : null,
    live: null,
    auto_rollback_rules: [],
  };
  return withDeployment(base, deployment);
};

/** Widen, narrow or restore the candidate's share; 100 completes the deployment. */
export const setRolloutShare = async (rollout: Rollout, pct: number) =>
  withDeployment(
    rollout,
    await ok<Deployment>(
      api.PATCH('/v1/deployments/{id}', {
        params: { path: { id: rollout.deployment_id } },
        body: {
          traffic_pct: pct,
        } satisfies Schemas['DeploymentPatch'],
      }),
    ),
  );

/** Every new run goes back to the baseline; runs already started keep their version. */
export const rollbackRollout = async (rollout: Rollout, reason: string) =>
  withDeployment(
    rollout,
    await ok<Deployment>(
      api.POST('/v1/deployments/{id}:rollback', {
        params: { path: { id: rollout.deployment_id } },
        body: {
          reason,
        } satisfies Schemas['DeploymentRollback'],
      }),
    ),
  );

/** A case added to the eval set, optionally frozen from a real run (eval_item.category). */
export type EvalCaseKind = Schemas['EvalItemCreate']['category'];

export interface NewEvalCase {
  source_run_id: Uuid | null;
  input: string;
  expected: string;
  kind: NonNullable<EvalCaseKind>;
}

/** POST /v1/eval-sets/{id}/items: input and expected are jsonb; the run it came from rides along. */
export const addEvalCase = async (evalSetId: string, body: NewEvalCase) =>
  ok<Schemas['EvalItemView']>(
    api.POST('/v1/eval-sets/{id}/items', {
      params: { path: { id: evalSetId } },
      body: {
        input: { text: body.input, ...(body.source_run_id ? { run_id: body.source_run_id } : {}) },
        expected: { text: body.expected },
        category: body.kind,
      } as unknown as Schemas['EvalItemCreate'],
    }),
  );

/* ─── Điều phối nhiều agent (UC-13-08) ────────────────────────────────── */

/** One agent's part of the team run: its task, what it found, its run's status. */
export interface DelegatedTask {
  run_id: Uuid;
  agent_id: Uuid;
  agent_name: string;
  /** Null for the lead agent; otherwise the agent that delegated (trigger_kind agent_call). */
  delegated_by: string | null;
  /** The lead's step that handed the task out; null when the step is not known. */
  task: string | null;
  /** That step's summary once the child answered. */
  result_text: string | null;
  status: RunStatus;
  cost: number;
}

/** A lead run and the runs it delegated, as one tree. */
export interface Orchestration {
  /** The lead run's id. */
  id: Uuid;
  code: string;
  status: RunStatus;
  lead_agent_name: string;
  started_at: string | null;
  /** Lead first, then the delegated runs in the order they were handed out. */
  tasks: DelegatedTask[];
  /** Total of every run in the tree. */
  cost: number;
}

type RunWire = Schemas['RunView'];
type RunRow = Schemas['RunSummary'];

const children = async (id: string) =>
  (
    await ok<Page<RunRow>>(
      api.GET('/v1/runs/{id}/children', { params: { path: { id }, query: { page_size: 500 } } }),
    )
  ).items;

/** UC-13-08 nests at most a few levels; each level is one read per parent. */
const MAX_DEPTH = 3;

export const getOrchestration = async (id: string): Promise<Orchestration> => {
  const [lead, agents] = await Promise.all([
    ok<RunWire>(
      api.GET('/v1/runs/{id}', { params: { path: { id }, query: { view: 'technical' } } }),
    ),
    listAgents().catch(() => null),
  ]);
  const nameOf = (agentId: string) => agents?.items.find((a) => a.id === agentId)?.name ?? 'Agent';
  // The step of a parent that started a child names the task and, once done, its result.
  const steps = new Map(
    (lead.steps ?? []).flatMap((s) => (s.child_run_id ? [[s.child_run_id, s]] : [])),
  );
  const tasks: DelegatedTask[] = [
    {
      run_id: lead.id,
      agent_id: lead.agent_id,
      agent_name: nameOf(lead.agent_id),
      delegated_by: null,
      task: null,
      result_text: null,
      status: lead.status as RunStatus,
      cost: num(lead.cost) ?? 0,
    },
  ];
  let level: { id: string; agent_id: string }[] = [lead];
  for (let depth = 0; depth < MAX_DEPTH && level.length; depth++) {
    const next = await Promise.all(level.map(async (p) => ({ p, rows: await children(p.id) })));
    level = [];
    for (const { p, rows } of next)
      for (const row of rows) {
        const step = steps.get(row.id);
        tasks.push({
          run_id: row.id,
          agent_id: row.agent_id,
          agent_name: nameOf(row.agent_id),
          delegated_by: nameOf(p.agent_id),
          task: step?.name ?? null,
          result_text: step?.summary ?? null,
          status: row.status as RunStatus,
          cost: num(row.cost) ?? 0,
        });
        level.push(row);
      }
  }
  return {
    id: lead.id,
    code: `#${lead.id.slice(0, 8)}`,
    status: lead.status as RunStatus,
    lead_agent_name: tasks[0].agent_name,
    started_at: lead.started_at,
    tasks,
    cost: tasks.reduce((sum, t) => sum + t.cost, 0),
  };
};

const ACTIVE = new Set(['queued', 'planning', 'running', 'waiting_human', 'paused']);

/*
 * A2 lists runs without a "has children" filter: a lead is found as the parent of a delegated
 * run (depth 1) on the latest page, preferring one still going.
 */
/** The agent whose deployment is still in progress; the rail opens that one. */
export const findRollout = async (): Promise<string | null> => {
  const page = await listAgents();
  return page.items.find((agent) => agent.active_deployment_id)?.id ?? null;
};

export const findOrchestration = async (): Promise<string | null> => {
  const page = await ok<Page<RunRow>>(
    api.GET('/v1/runs', { params: { query: { page_size: 500 } } }),
  );
  const leads = [
    ...new Set(
      page.items.flatMap((r) => (r.depth === 1 && r.parent_run_id ? [r.parent_run_id] : [])),
    ),
  ];
  const active = leads.find((id) => {
    const lead = page.items.find((r) => r.id === id);
    return lead ? ACTIVE.has(lead.status) : false;
  });
  return active ?? leads[0] ?? null;
};

/** Pause or resume the lead; its delegated runs wait on it. */
export const controlOrchestration = async (id: string, verb: 'pause' | 'resume') => {
  await controlRun(id, verb);
  return getOrchestration(id);
};
