import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { RunStatus } from './types';

/*
 * Agent operations of P13: trying a new config version against the eval set and in parallel on
 * live traffic, then widening or rolling it back (UC-13-04, UC-13-05), and a team of agents
 * working one problem under a lead agent (UC-13-08).
 *
 * The system design has agent_def.config_version, eval_run and agent_run.trigger_kind =
 * 'agent_call', but no resource for a rollout nor for a delegation tree, so every resource and
 * endpoint below is a PROPOSAL for the backend; the mock (mocks/agentOps.ts) serves them.
 */
type Uuid = string;
type Timestamp = string;

/* ─── Đánh giá & triển khai (UC-13-04, UC-13-05) ─────────────────────── */

/** PROPOSAL: how a criterion is measured, so the screen formats it ("97,5%", "8.400 đ", "6 phút"). */
export type EvalUnit = 'percent' | 'vnd' | 'minutes';

/** PROPOSAL: one row of "Bộ đánh giá": the baseline and the candidate on the same eval set. */
export interface EvalCriterion {
  key: string;
  label: string;
  unit: EvalUnit;
  baseline: number;
  candidate: number;
  threshold: { op: 'gte' | 'lte' | 'eq'; value: number };
}

/** PROPOSAL: one version's numbers on live traffic over `window_days`. */
export interface LiveStats {
  runs: number;
  needs_approval_pct: number;
  errors: number;
}

export type RolloutStatus = 'canary' | 'held' | 'completed' | 'rolled_back';

/** PROPOSAL: GET /v1/agents/{id}/rollout — the version being tried and the one it may replace. */
export interface Rollout {
  agent_id: Uuid;
  agent_name: string;
  baseline_version: number;
  candidate_version: number;
  /** Share of new runs the candidate takes, 0–100. */
  candidate_share_pct: number;
  /** The next step the owner can widen to ("Tăng lên 100%"); null once at 100. */
  next_share_pct: number | null;
  /** The share before the last change, so a toast can undo it ("Lùi lại", "Hoàn tác"). */
  previous_share_pct: number | null;
  status: RolloutStatus;
  /** What the candidate changes, in words, from the config version's change note. */
  change_summary: string;
  eval_set: { id: Uuid; case_count: number; criteria: EvalCriterion[] };
  live: { window_days: number; baseline: LiveStats; candidate: LiveStats };
  /** Automatic rollback conditions, in words ("Tự quay lui khi"). */
  auto_rollback_rules: string[];
  updated_at: Timestamp;
}

/** PROPOSAL: a case added to the eval set, optionally frozen from a real run. */
export type EvalCaseKind = 'correct_conclusion' | 'ask_on_time' | 'permission' | 'cost' | 'latency';

export interface NewEvalCase {
  source_run_id: Uuid | null;
  input: string;
  expected: string;
  kind: EvalCaseKind;
}

export interface EvalCase extends NewEvalCase {
  id: Uuid;
  eval_set_id: Uuid;
  created_at: Timestamp;
}

/** PROPOSAL: the agents with a version in canary, for opening the screen without an agent. */
export const listRollouts = async (): Promise<Page<Rollout>> => {
  try {
    return await v1Get<Page<Rollout>>('/rollouts', { status: 'canary,held', page_size: 50 });
  } catch (error) {
    // The rollout collection is a proposal until the API-05 deployment read model lands.
    // A missing collection therefore means there is no candidate to show, not a broken page.
    if (error instanceof V1Error && error.status === 404)
      return { items: [], next_page_token: '' };
    throw error;
  }
};

export const getRollout = async (agentId: string) =>
  v1Get<Rollout>(`/agents/${pathId(agentId)}/rollout`);

/** PROPOSAL: widen, narrow or restore the candidate's share. */
export const setRolloutShare = async (agentId: string, candidateSharePct: number) =>
  v1Send<Rollout>('POST', `/agents/${pathId(agentId)}/rollout:set`, {
    candidate_share_pct: candidateSharePct,
  });

/** PROPOSAL: keep the current share and record the decision, so it stops asking. */
export const holdRollout = async (agentId: string) =>
  v1Send<Rollout>('POST', `/agents/${pathId(agentId)}/rollout:hold`);

/** PROPOSAL: every new run goes back to the baseline; runs already started keep their version. */
export const rollbackRollout = async (agentId: string) =>
  v1Send<Rollout>('POST', `/agents/${pathId(agentId)}/rollout:rollback`);

export const addEvalCase = async (evalSetId: string, body: NewEvalCase) =>
  v1Send<EvalCase>('POST', `/eval-sets/${pathId(evalSetId)}/cases`, body);

/* ─── Điều phối nhiều agent (UC-13-08) ────────────────────────────────── */

/** PROPOSAL: one agent's part of the team run: its task, what it found, its run's status. */
export interface DelegatedTask {
  run_id: Uuid;
  agent_id: Uuid;
  agent_name: string;
  /** Null for the lead agent; otherwise the agent that delegated (agent_run.trigger_kind = agent_call). */
  delegated_by: string | null;
  task: string;
  /** The latest result or what it is doing, one sentence. */
  result_text: string | null;
  status: RunStatus;
  cost: string;
}

/** PROPOSAL: GET /v1/orchestrations/{id} — a lead run and the runs it delegated, as one tree. */
export interface Orchestration {
  /** The lead run's id. */
  id: Uuid;
  code: string;
  title: string;
  status: RunStatus;
  lead_agent_name: string;
  started_at: Timestamp;
  /** What started it, "cảnh báo #A-2291". */
  started_from: string;
  /** Lead first, then the delegated runs in the order they were handed out. */
  tasks: DelegatedTask[];
  /** The team's guardrails in words ("Quy tắc điều phối"). */
  rules: string[];
  /** Total of every run in the tree. */
  cost: string;
}

export const listOrchestrations = (query: { status?: RunStatus } = {}) =>
  v1Get<Page<Orchestration>>('/orchestrations', { page_size: 50, ...query });

export const getOrchestration = async (id: string) =>
  v1Get<Orchestration>(`/orchestrations/${pathId(id)}`);

/** PROPOSAL: pause or resume the lead and every delegated run that has not finished. */
export const controlOrchestration = async (id: string, verb: 'pause' | 'resume') =>
  v1Send<Orchestration>('POST', `/orchestrations/${pathId(id)}:${verb}`);
