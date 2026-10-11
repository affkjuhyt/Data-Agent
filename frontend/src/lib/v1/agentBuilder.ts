import { pathId, v1Get, v1Send, type Page } from './client';
import type { AgentDef, AgentRun } from './types';

/*
 * P13 agent authoring on the v1 API ("Đặc tả API" A2/A3, group Agent; tables agent.agent_config,
 * agent_tool, agent_trigger, agent_memory of tab 3): draft a config from words (UC-13-01), save
 * config versions and triggers (UC-13-02/03), test-run a version (UC-13-04), deploy it
 * (UC-13-05) and manage the agent's memory (UC-13-07).
 *
 * Fields marked PROPOSAL are what the screens need and the spec does not have yet.
 */

type Uuid = string;
type Timestamp = string;

export type Autonomy = 'suggest_only' | 'propose_approve' | 'act_then_report';
export type ConfigStatus = 'draft' | 'testing' | 'passed' | 'deployed' | 'superseded';

/** agent.agent_tool */
export interface AgentTool {
  tool: string;
  permission_level: 'read' | 'write_derived' | 'send_external';
  requires_checkpoint: boolean;
}

/** One "Agent phải dừng và hỏi khi" condition (agent_config.checkpoints item). */
export interface Checkpoint {
  id: string;
  /** PROPOSAL: the condition in words; the spec stores checkpoints as free jsonb. */
  text: string;
  enabled: boolean;
}

/** agent.agent_config: one version; editing always makes a new one (BR-69). */
export interface AgentConfig {
  id: Uuid;
  agent_id: Uuid;
  version: number;
  goal: string;
  data_scope: Record<string, unknown>;
  skill_ids: Uuid[];
  autonomy: Autonomy;
  checkpoints: Checkpoint[];
  denied_actions: string[];
  limits: Record<string, number>;
  change_note: string | null;
  status: ConfigStatus;
  tools: AgentTool[];
  created_at: Timestamp;
  /** PROPOSAL: data_scope in words, "mart.credit_risk, Core banking (chỉ đọc)". */
  data_scope_text: string;
}

export type TriggerKind = 'schedule' | 'event' | 'threshold' | 'agent_call' | 'manual';

/** agent.agent_trigger */
export interface AgentTrigger {
  id: Uuid;
  kind: TriggerKind;
  cron: string | null;
  event_type: string | null;
  condition: Record<string, unknown>;
  enabled: boolean;
  /**
   * PROPOSAL: the condition in words ("Thứ Hai 07:00"). On PUT a changed description without
   * cron/condition asks the server to parse it, as draft-from-text does.
   */
  description: string;
}

/** GET /v1/agents/{id}: AgentDef with its running config and triggers. */
export interface AgentDetail extends AgentDef {
  /** "Kèm phiên bản cấu hình đang chạy": the latest version, the one the page edits. */
  config: AgentConfig;
  /** PROPOSAL: the spec has PUT /triggers but no read; the detail carries them. */
  triggers: AgentTrigger[];
  /** PROPOSAL: whose permissions the agent runs under (BR-147: the owner). */
  owner: { id: Uuid; name: string };
}

/* ─── UC-13-01: draft from a description ─────────────────────────────── */

export interface DraftRequest {
  description: string;
  template_ref?: string;
  /** PROPOSAL: the user's corrections of the proposed lines, re-read by the config agent. */
  corrections?: Partial<Record<DraftField, string>>;
}

export type DraftField =
  | 'name'
  | 'goal'
  | 'trigger'
  | 'data'
  | 'skills'
  | 'recipients'
  | 'allowed'
  | 'checkpoints';

/** PROPOSAL: one row of "Agent hiểu như sau", with a flag when the user should look at it. */
export interface DraftLine {
  field: DraftField;
  label: string;
  text: string;
  flag: string | null;
}

/**
 * POST /v1/agents:draft-from-text. The spec returns an AgentConfig "phần vượt quyền chủ sở hữu
 * được đánh dấu"; PROPOSAL: the name, kind and triggers of the new agent, the readable lines and
 * a warning sentence (uncertified data) come along so the screen can show them before saving.
 */
export interface AgentDraft {
  name: string;
  code: string;
  kind: AgentDef['kind'];
  config: Omit<AgentConfig, 'id' | 'agent_id' | 'version' | 'status' | 'created_at'>;
  triggers: Omit<AgentTrigger, 'id'>[];
  lines: DraftLine[];
  warning: string | null;
}

/** PROPOSAL: the "Hoặc bắt đầu từ mẫu" list; the spec has template_ref but no endpoint for them. */
export interface AgentTemplate {
  ref: string;
  title: string;
  pack: string;
  description: string;
}

/* ─── UC-13-04: test run ──────────────────────────────────────────────── */

/**
 * PROPOSAL: the report of a test run (agent_run with is_test), for the "Chạy thử" drawer:
 * the four checks on top, what would have been sent, and the failed test cases.
 */
export interface TestReport {
  completed: boolean;
  human_questions: number;
  policy_violations: number;
  eval_passed: number;
  eval_total: number;
  preview: { label: string; text: string } | null;
  failures: string[];
  /** Where the run read its data, "Trên dữ liệu tuần 40". */
  data_text: string;
}

/** PROPOSAL: one step told for the drawer ("Agent đã làm"). */
export interface TestStep {
  title: string;
  detail: string;
  state: 'done' | 'asked' | 'error';
}

export interface TestRun extends Pick<AgentRun, 'id' | 'agent_id' | 'config_version' | 'status' | 'cost' | 'started_at' | 'finished_at'> {
  is_test: true;
  /** PROPOSAL: the agent's name, for the drawer title. */
  agent_name: string;
  /** PROPOSAL */
  test_steps: TestStep[];
  /** PROPOSAL: null until the run finishes. */
  test_report: TestReport | null;
}

/** agent.agent_deployment */
export interface AgentDeployment {
  id: Uuid;
  agent_id: Uuid;
  config_version: number;
  traffic_pct: number;
  status: 'pending_approval' | 'rolling' | 'completed' | 'rolled_back' | 'cancelled';
}

/* ─── UC-13-07: memory ────────────────────────────────────────────────── */

export type MemoryScope = 'agent' | 'user' | 'data_space';

/**
 * PROPOSAL: when a memory stops applying ("Hạn" of "Hộp thoại · addMemory" 2095:8909): after 30 or
 * 90 days, once people correct the agent twice where it applied, or never.
 */
export type MemoryExpiry = 'days_30' | 'days_90' | 'until_wrong_2' | 'never';

/** agent.agent_memory */
export interface AgentMemory {
  id: Uuid;
  agent_id: Uuid | null;
  scope: MemoryScope;
  user_id: Uuid | null;
  kind: 'decision' | 'preference' | 'data_insight' | 'domain_context';
  content: string;
  source_kind: 'hitl' | 'run' | 'manual' | 'pack';
  status: 'pending_review' | 'active' | 'disabled' | 'conflict';
  use_count: number;
  created_at: Timestamp;
  /** PROPOSAL: where it came from, in words ("Phạm Đức Long sửa cách hiểu, 02/10"). */
  source_text: string;
  /** PROPOSAL: the scope in words ("Riêng Đỗ Hải Yến", "Toàn tổ chức"). */
  scope_text: string;
  /** PROPOSAL: the last run that used it; null when never used. */
  last_used_at: Timestamp | null;
  /** PROPOSAL: see MemoryExpiry. */
  expiry: MemoryExpiry;
}

export interface NewMemory {
  scope: MemoryScope;
  kind: AgentMemory['kind'];
  content: string;
  user_id?: Uuid;
  /** PROPOSAL: defaults to days_90 when omitted. */
  expiry?: MemoryExpiry;
}

/** PROPOSAL: the fields "Hộp thoại · editMemory" (2095:8961) changes. */
export type MemoryPatch = Pick<AgentMemory, 'content' | 'scope' | 'expiry'>;

/* ─── Endpoints ───────────────────────────────────────────────────────── */

export const listTemplates = () => v1Get<Page<AgentTemplate>>('/agent-templates', { page_size: 50 });

export const draftAgent = (body: DraftRequest) =>
  v1Send<AgentDraft>('POST', '/agents:draft-from-text', body);

/** POST /v1/agents: owner is the caller (BR-147); the agent starts as a draft. */
export const createAgent = (draft: AgentDraft, templateRef?: string) =>
  v1Send<AgentDetail>('POST', '/agents', {
    code: draft.code,
    name: draft.name,
    kind: draft.kind,
    template_ref: templateRef,
    config: draft.config,
    // PROPOSAL: the triggers in the same call, so a new agent is one request.
    triggers: draft.triggers,
  });

export const getAgent = async (id: string) => v1Get<AgentDetail>(`/agents/${pathId(id)}`);

export const listConfigs = async (id: string) => v1Get<Page<AgentConfig>>(`/agents/${pathId(id)}/configs`);

export type ConfigInput = Pick<AgentConfig, 'goal' | 'autonomy' | 'checkpoints' | 'denied_actions' | 'tools'> & {
  change_note?: string;
};

export const saveConfig = async (id: string, body: ConfigInput) =>
  v1Send<AgentConfig>('POST', `/agents/${pathId(id)}/configs`, body);

export const putTriggers = async (id: string, triggers: AgentTrigger[]) =>
  v1Send<AgentTrigger[]>('PUT', `/agents/${pathId(id)}/triggers`, { triggers });

/** 202 with the run; the spec gives a Location, the mock and (PROPOSAL) the backend the body too. */
export const startTestRun = async (id: string, configVersion: number) =>
  v1Send<TestRun>('POST', `/agents/${pathId(id)}/test-runs`, { config_version: configVersion });

export const getTestRun = async (runId: string) => v1Get<TestRun>(`/runs/${pathId(runId)}`);

/** "Kích hoạt": full traffic, which waits for the data owner's approval. */
export const activateAgent = async (id: string, configVersion: number) =>
  v1Send<AgentDeployment>('POST', `/agents/${pathId(id)}/deployments`, {
    config_version: configVersion,
    traffic_pct: 100,
  });

export const listMemories = async (id: string) =>
  v1Get<Page<AgentMemory>>(`/agents/${pathId(id)}/memories`, { page_size: 500 });

export const addMemory = async (id: string, body: NewMemory) =>
  v1Send<AgentMemory>('POST', `/agents/${pathId(id)}/memories`, body);

/** PROPOSAL: PATCH /v1/memories/{id}; applies from the next run (a data_space scope re-enters review). */
export const updateMemory = async (memoryId: string, body: MemoryPatch) =>
  v1Send<AgentMemory>('PATCH', `/memories/${pathId(memoryId)}`, body);

export const forgetMemory = async (memoryId: string) => v1Send<void>('DELETE', `/memories/${pathId(memoryId)}`);
