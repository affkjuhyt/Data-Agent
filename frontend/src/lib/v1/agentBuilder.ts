import { readAgent } from './api';
import { type Page, api, ok, okAs } from './client';
import type { components } from './schema';
import type { AgentDef, AgentRun, AgentWire } from './types';
import { getOnboarding } from './explore';

/*
 * P13 agent authoring on the v1 API ("Đặc tả API" A2/A3, group Agent; tables agent.agent_config,
 * agent_tool, agent_trigger, agent_memory of tab 3): draft a config from words (UC-13-01), save
 * config versions and triggers (UC-13-02/03), test-run a version (UC-13-04), deploy it
 * (UC-13-05) and manage the agent's memory (UC-13-07).
 *
 * Fields marked PROPOSAL are what the screens need and the spec does not have yet. The api's
 * shapes (schema.ts) are read into the screens' here; a missing PROPOSAL field gets a value
 * derived from what the api sent, or is left empty.
 */

type Schemas = components['schemas'];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

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
  config: AgentConfig | null;
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

export interface TestRun extends Pick<
  AgentRun,
  'id' | 'agent_id' | 'config_version' | 'status' | 'cost' | 'started_at' | 'finished_at'
> {
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

/** What "Hộp thoại · editMemory" (2095:8961) changes; the api keeps the scope (MemoryPatch). */
export type MemoryPatch = Pick<AgentMemory, 'content' | 'expiry'>;

/* ─── Reading the api's shapes ───────────────────────────────────────── */

/** agent_config.checkpoints is free jsonb: a sentence, or {id, text, enabled}. */
const readCheckpoints = (raw: unknown): Checkpoint[] =>
  (Array.isArray(raw) ? raw : []).flatMap((c, i): Checkpoint[] =>
    typeof c === 'string'
      ? [{ id: `cp-${i}`, text: c, enabled: true }]
      : isRecord(c) && typeof c.text === 'string'
        ? [
            {
              id: typeof c.id === 'string' ? c.id : `cp-${i}`,
              text: c.text,
              enabled: c.enabled !== false,
            },
          ]
        : [],
  );

/** The data scope in words when the PROPOSAL `data_scope_text` is missing: its keys and values. */
const scopeText = (scope: Record<string, unknown>) =>
  Object.entries(scope)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)
    .join(' · ');

type ConfigWire = (Schemas['ConfigView'] | Schemas['ConfigDraft']) & { data_scope_text?: string };

function readConfigFields(raw: ConfigWire) {
  const scope = isRecord(raw.data_scope) ? raw.data_scope : {};
  return {
    goal: raw.goal,
    data_scope: scope,
    data_scope_text: raw.data_scope_text ?? scopeText(scope),
    skill_ids: raw.skill_ids,
    autonomy: raw.autonomy as Autonomy,
    checkpoints: readCheckpoints(raw.checkpoints),
    denied_actions: raw.denied_actions,
    limits: (isRecord(raw.limits) ? raw.limits : {}) as Record<string, number>,
    tools: (raw.tools ?? []).map((t) => ({
      tool: t.tool,
      permission_level: t.permission_level as AgentTool['permission_level'],
      requires_checkpoint: t.requires_checkpoint ?? false,
    })),
  };
}

const readConfig = (raw: Schemas['ConfigView'] & { data_scope_text?: string }): AgentConfig => ({
  ...readConfigFields(raw),
  id: raw.id,
  agent_id: raw.agent_id,
  version: raw.version,
  change_note: raw.change_note,
  status: raw.status as ConfigStatus,
  created_at: raw.created_at,
});

const readTrigger = (raw: Schemas['TriggerView'] & { description?: string }): AgentTrigger => ({
  id: raw.id,
  kind: raw.kind as TriggerKind,
  cron: raw.cron,
  event_type: raw.event_type,
  condition: isRecord(raw.condition) ? raw.condition : {},
  enabled: raw.enabled,
  description: raw.description ?? raw.cron ?? raw.event_type ?? raw.kind,
});

/** PROPOSAL: the triggers and the owner's name come with the detail; the api has neither read. */
type AgentDetailWire = Schemas['AgentView'] &
  Pick<AgentWire, 'goal' | 'now' | 'trigger_text' | 'permission_text' | 'runs_7d'> & {
    current_config?: (Schemas['ConfigView'] & { data_scope_text?: string }) | null;
    triggers?: Schemas['TriggerView'][];
    owner?: { id: Uuid; name: string };
  };

const readAgentDetail = (raw: AgentDetailWire): AgentDetail => ({
  ...readAgent(raw),
  config: raw.current_config ? readConfig(raw.current_config) : null,
  triggers: (raw.triggers ?? []).map(readTrigger),
  owner: raw.owner ?? { id: raw.owner_user_id, name: '' },
});

const LINE_LABEL: Record<DraftField, string> = {
  name: 'Tên',
  goal: 'Mục tiêu',
  trigger: 'Khi nào chạy',
  data: 'Dữ liệu',
  skills: 'Kỹ năng',
  recipients: 'Gửi cho',
  allowed: 'Được làm',
  checkpoints: 'Dừng và hỏi khi',
};

const PERMISSION_TEXT: Record<AgentTool['permission_level'], string> = {
  read: 'đọc',
  write_derived: 'ghi bảng dẫn xuất',
  send_external: 'gửi ra ngoài',
};

/** A code from the name: ASCII capitals and underscores, "Đối soát dư nợ" → "DOI_SOAT_DU_NO". */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[đĐ]/g, 'D')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || 'AGENT';

type DraftWire = Schemas['ConfigDraft'] &
  Partial<Pick<AgentDraft, 'name' | 'code' | 'kind' | 'lines' | 'warning'>> & {
    triggers?: AgentDraft['triggers'];
    data_scope_text?: string;
  };

/**
 * The api drafts the config only (ConfigDraft). The name, code and kind of the new agent and the
 * "Agent hiểu như sau" lines are PROPOSAL; without them they come from the description and the
 * drafted config, and what lies outside the owner's rights becomes the warning.
 */
function readDraft(raw: DraftWire, description: string): AgentDraft {
  const config = { ...readConfigFields(raw), change_note: null };
  const name = raw.name ?? description.trim().split(/[.\n]/)[0].slice(0, 80);
  const line = (field: DraftField, text: string): DraftLine => ({
    field,
    label: LINE_LABEL[field],
    text,
    flag: null,
  });
  const lines =
    raw.lines ??
    [
      line('goal', config.goal),
      line('data', config.data_scope_text),
      line(
        'allowed',
        config.tools.map((t) => `${t.tool} (${PERMISSION_TEXT[t.permission_level]})`).join(', '),
      ),
      line('checkpoints', config.checkpoints.map((c) => c.text).join('; ')),
    ].filter((l) => l.text);
  return {
    name,
    code: raw.code ?? codeOf(name),
    kind: raw.kind ?? 'custom',
    config,
    triggers: raw.triggers ?? [],
    lines,
    warning:
      raw.warning ??
      (raw.out_of_scope.length > 0 ? `Ngoài quyền của bạn: ${raw.out_of_scope.join(', ')}.` : null),
  };
}

type TestRunWire = Schemas['RunView'] &
  Partial<Pick<TestRun, 'agent_name' | 'test_steps' | 'test_report'>>;

/** PROPOSAL: the drawer's steps and report; without them it shows the run's status only. */
const readTestRun = (raw: TestRunWire): TestRun => ({
  id: raw.id,
  agent_id: raw.agent_id,
  config_version: raw.config_version,
  status: raw.status as TestRun['status'],
  cost: raw.cost,
  started_at: raw.started_at,
  finished_at: raw.finished_at,
  is_test: true,
  agent_name: raw.agent_name ?? '',
  test_steps: raw.test_steps ?? [],
  test_report: raw.test_report ?? null,
});

const DAY = 86_400_000;

/** The PROPOSAL expiry as the api stores it: a date, or none ("until wrong twice" is not stored). */
const expiresAt = (expiry: MemoryExpiry | undefined) =>
  expiry === 'days_30'
    ? new Date(Date.now() + 30 * DAY).toISOString()
    : expiry === 'never' || expiry === 'until_wrong_2'
      ? null
      : new Date(Date.now() + 90 * DAY).toISOString();

const SCOPE_TEXT: Record<MemoryScope, string> = {
  agent: 'Riêng agent này',
  user: 'Riêng một người',
  data_space: 'Cả Data Space',
};

type MemoryWire = Schemas['MemoryView'] &
  Partial<Pick<AgentMemory, 'source_text' | 'scope_text' | 'last_used_at' | 'expiry'>>;

function readMemory(raw: MemoryWire): AgentMemory {
  const days = raw.expires_at
    ? (new Date(raw.expires_at).getTime() - new Date(raw.created_at).getTime()) / DAY
    : null;
  return {
    id: raw.id,
    agent_id: raw.agent_id,
    scope: raw.scope as MemoryScope,
    user_id: raw.user_id,
    kind: raw.kind as AgentMemory['kind'],
    content: raw.content,
    source_kind: raw.source_kind as AgentMemory['source_kind'],
    status: raw.status as AgentMemory['status'],
    use_count: raw.use_count,
    created_at: raw.created_at,
    source_text: raw.source_text ?? '',
    scope_text: raw.scope_text ?? SCOPE_TEXT[raw.scope as MemoryScope] ?? raw.scope,
    last_used_at: raw.last_used_at ?? null,
    expiry: raw.expiry ?? (days === null ? 'never' : days <= 45 ? 'days_30' : 'days_90'),
  };
}

/* ─── Endpoints ───────────────────────────────────────────────────────── */

/** The onboarding contract is the canonical source for the templates used by agent authoring. */
export const listTemplates = async (): Promise<Page<AgentTemplate>> => {
  const onboarding = await getOnboarding();
  return {
    items: onboarding.templates.map((template) => ({
      ref: template.ref,
      title: template.title,
      pack: 'onboarding',
      description: template.description,
    })),
    next_page_token: '',
  };
};

export const draftAgent = async (body: DraftRequest) =>
  readDraft(
    await ok<DraftWire>(api.POST('/v1/agents:draft-from-text', { body })),
    body.description,
  );

/*
 * The config fields of ConfigCreate; the api ignores the rest, so only these are sent. Not typed
 * as ConfigCreate: openapi-typescript reads its dict[str, Any] columns as Record<string, never>.
 */
const configBody = (config: ConfigInput) => ({
  goal: config.goal,
  autonomy: config.autonomy,
  checkpoints: config.checkpoints,
  denied_actions: config.denied_actions,
  tools: config.tools,
  ...(config.data_scope ? { data_scope: config.data_scope } : {}),
  ...(config.skill_ids ? { skill_ids: config.skill_ids } : {}),
  ...(config.limits ? { limits: config.limits } : {}),
  ...(config.change_note ? { change_note: config.change_note } : {}),
});

/**
 * POST /v1/agents: owner is the caller (BR-147); the agent starts as a draft. AgentCreate has no
 * triggers, so they follow in PUT /triggers.
 */
export const createAgent = async (draft: AgentDraft, templateRef?: string) => {
  const created = await okAs<AgentDetailWire>(
    api.POST('/v1/agents', {
      body: {
        code: draft.code,
        name: draft.name,
        kind: draft.kind,
        template_ref: templateRef,
        config: configBody(draft.config),
      },
    }),
  );
  if (draft.triggers.length > 0) await putTriggers(created.id, draft.triggers);
  return readAgentDetail(created);
};

export const getAgent = async (id: string) =>
  readAgentDetail(
    await okAs<AgentDetailWire>(api.GET('/v1/agents/{id}', { params: { path: { id } } })),
  );

export const listConfigs = async (id: string) => {
  const page = await ok<Page<Schemas['ConfigView']>>(
    api.GET('/v1/agents/{id}/configs', { params: { path: { id } } }),
  );
  return { ...page, items: page.items.map(readConfig) };
};

export type ConfigInput = Pick<
  AgentConfig,
  'goal' | 'autonomy' | 'checkpoints' | 'denied_actions' | 'tools'
> &
  Partial<Pick<AgentConfig, 'data_scope' | 'skill_ids' | 'limits'>> & {
    change_note?: string | null;
  };

export const saveConfig = async (id: string, body: ConfigInput) =>
  readConfig(
    await ok<Schemas['ConfigView']>(
      api.POST('/v1/agents/{id}/configs', { params: { path: { id } }, body: configBody(body) }),
    ),
  );

/** PUT replaces the whole set (A1.3); the PROPOSAL description travels for the server to parse. */
export const putTriggers = async (id: string, triggers: Omit<AgentTrigger, 'id'>[]) => {
  const page = await ok<Page<Schemas['TriggerView']>>(
    api.PUT('/v1/agents/{id}/triggers', {
      params: { path: { id } },
      body: {
        triggers,
      },
    }),
  );
  return page.items.map(readTrigger);
};

/** 202 with the run's id (RunAccepted); the drawer then reads the run. */
export const startTestRun = async (id: string, configVersion: number) => {
  const accepted = await ok<Schemas['RunAccepted']>(
    api.POST('/v1/agents/{id}/test-runs', {
      params: { path: { id } },
      body: {
        config_version: configVersion,
      },
    }),
  );
  return getTestRun(accepted.id);
};

export const getTestRun = async (runId: string) =>
  readTestRun(await ok<TestRunWire>(api.GET('/v1/runs/{id}', { params: { path: { id: runId } } })));

/** "Kích hoạt": full traffic, which waits for the data owner's approval. */
export const activateAgent = async (id: string, configVersion: number) =>
  okAs<AgentDeployment>(
    api.POST('/v1/agents/{id}/deployments', {
      params: { path: { id } },
      body: {
        config_version: configVersion,
        traffic_pct: 100,
      },
    }),
  );

export const listMemories = async (id: string) => {
  const page = await ok<Page<MemoryWire>>(
    api.GET('/v1/agents/{id}/memories', { params: { path: { id }, query: { page_size: 500 } } }),
  );
  return { ...page, items: page.items.map(readMemory) };
};

export const addMemory = async (id: string, body: NewMemory) =>
  readMemory(
    await ok<MemoryWire>(
      api.POST('/v1/agents/{id}/memories', {
        params: { path: { id } },
        body: {
          scope: body.scope,
          kind: body.kind,
          content: body.content,
          user_id: body.user_id,
          expires_at: expiresAt(body.expiry),
        } satisfies Schemas['MemoryCreate'],
      }),
    ),
  );

/**
 * Applies from the next run (a data_space scope re-enters review). MemoryPatch changes the content
 * and the expiry; the scope is fixed once the memory exists.
 */
export const updateMemory = async (memoryId: string, body: MemoryPatch) =>
  readMemory(
    await ok<MemoryWire>(
      api.PATCH('/v1/memories/{id}', {
        params: { path: { id: memoryId } },
        body: {
          content: body.content,
          expires_at: expiresAt(body.expiry),
        } satisfies Schemas['MemoryPatch'],
      }),
    ),
  );

export const forgetMemory = async (memoryId: string) =>
  ok<void>(api.DELETE('/v1/memories/{id}', { params: { path: { id: memoryId } } }));
