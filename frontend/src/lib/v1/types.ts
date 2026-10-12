import type { components } from './schema';
/*
 * Resources of the v1 API, as the system design defines them: a PascalCase schema is the DB
 * table of the same name (tab "Thiết kế cơ sở dữ liệu", D4) without org_id, created_by,
 * updated_by, deleted_at and secret references (tab "Đặc tả API", A3). Only the fields the
 * screens read are typed; the rest of the row may be present.
 *
 * Fields marked PROPOSAL are not in the spec yet: the screens need them and the backend is
 * asked to add them (docs/v3/05-ui-v3.md §4 lists them). The mock serves them.
 */

type Uuid = string;
/** RFC 3339 with time zone (A1.1). */
type Timestamp = string;

/*
 * openapi-typescript marks a field with a server default as required, right for a response and
 * wrong for a request body: the server fills what the client leaves out.
 */
type RequestBody<T, Defaulted extends keyof T> = Omit<T, Defaulted> & Partial<Pick<T, Defaulted>>;

/* ─── Cần bạn: inbox.hitl_request ─────────────────────────────────────── */

export type HitlKind =
  | 'approve_proposal'
  | 'conflict'
  | 'clarification'
  | 'checkpoint'
  | 'budget'
  | 'access'
  | 'export'
  | 'writeback'
  | 'period_exception'
  | 'retro_change'
  | 'deploy'
  | 'memory_share'
  | 'schema_drift'
  | 'report_publish'
  | 'data_review'
  | 'entity_match'
  | 'forecast_assumption'
  | 'forecast_model'
  | 'legal_review'
  | 'knowledge_finding';

export type HitlStatus =
  | 'open'
  | 'answered'
  | 'approved'
  | 'rejected'
  | 'changes_requested'
  | 'reassigned'
  | 'expired'
  | 'cancelled';

/** One choice in `options` (jsonb "Các phương án"). PROPOSAL: the inner shape. */
export interface HitlOption {
  id: string;
  label: string;
  /** "Khớp số đã nộp; tăng 0,22 điểm" — what choosing it leads to (spec "Thẻ việc Cần bạn"). */
  consequence: string;
  /** The agent's recommendation, preselected. */
  is_default?: boolean;
}

/**
 * GET /v1/inbox and /v1/hitl-requests/{id} as the api sends them: `context` and `options` are
 * jsonb the producer fills. PROPOSAL: `agent`, the agent that asked, joined from source_ref.
 */
export type HitlWire = components['schemas']['HitlView'] & {
  approval_steps?: components['schemas']['ApprovalStepView'][];
  agent?: { id: Uuid; name: string } | null;
};

/** A HITL request as the screens read it; `readHitl` (lib/v1/api.ts) narrows the jsonb columns. */
export type HitlRequest = Omit<
  HitlWire,
  'kind' | 'risk' | 'status' | 'context' | 'options' | 'agent'
> & {
  kind: HitlKind;
  risk: 'low' | 'medium' | 'high';
  status: HitlStatus;
  /** "Dữ liệu để người quyết định". PROPOSAL: `why` carries the "Vì sao agent hỏi" sentence. */
  context: { why?: string } & Record<string, unknown>;
  /** The options that have an id and a label; anything else in the column is dropped. */
  options: HitlOption[];
  agent: { id: Uuid; name: string } | null;
};

/** POST /v1/hitl-requests/{id}:resolve */
export type ResolveHitl = RequestBody<components['schemas']['HitlResolve'], 'remember_as_rule'>;

/* ─── Agents: agent.agent_def, agent.agent_run ────────────────────────── */

export type AgentStatusV1 = 'draft' | 'testing' | 'running' | 'paused' | 'retired';

/** PROPOSAL, for the "Đội agent" table: fields the api does not send yet. */
interface AgentDisplay {
  /** The goal of the running config version. */
  goal: string;
  /** What it is doing now, one sentence, and its tone. */
  now: { text: string; tone: 'normal' | 'waiting' | 'error' };
  /** The triggers in words, "Khi khoá sổ · thứ Hai 07:00". */
  trigger_text: string;
  /** What it may do, "Đọc · đề xuất". */
  permission_text: string;
  /** Last 7 days, "12 lượt · 58% cần duyệt". */
  runs_7d: { count: number; needs_approval_pct: number; errors: number };
}

/** GET /v1/agents as the api sends it, plus the PROPOSAL fields when present. */
export type AgentWire = components['schemas']['AgentSummary'] & Partial<AgentDisplay>;

/** An agent as the screens read it; `readAgent` (lib/v1/api.ts) leaves a missing PROPOSAL field null. */
export type AgentDef = Omit<
  AgentWire,
  'kind' | 'status' | 'active_runs' | 'waiting_runs' | keyof AgentDisplay
> & {
  kind:
    | 'analysis'
    | 'monitor'
    | 'report'
    | 'reconcile'
    | 'data_prep'
    | 'data_quality'
    | 'document'
    | 'custom';
  status: AgentStatusV1;
  /** "Kèm số lượt chạy đang chạy, chờ người, chi phí tháng". */
  active_runs: number;
  waiting_runs: number;
  goal: string | null;
  now: AgentDisplay['now'] | null;
  trigger_text: string | null;
  permission_text: string | null;
  runs_7d: AgentDisplay['runs_7d'] | null;
};

export type RunStatus =
  | 'queued'
  | 'planning'
  | 'running'
  | 'waiting_human'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

/** One sentence of a run's story ("Spec · v3.1 · Kể việc của agent"). PROPOSAL: the shape of `narrative`. */
export interface NarrativeEvent {
  at: Timestamp;
  text: string;
  /** done = already happened; now = the step under way (with progress); next = what comes after. */
  state: 'done' | 'now' | 'next';
  progress?: number;
}

/** One step of the plan beside the story. PROPOSAL: from the run's current plan version. */
export interface RunPlanStep {
  title: string;
  state: 'done' | 'running' | 'pending_approval' | 'error' | 'waiting';
  /** "02:09", "3 lần thử theo chính sách", "Chưa chạy" — the line under the title. */
  detail?: string;
  /** "0:31" */
  duration_text?: string;
  /** PROPOSAL, for "P13 · Can thiệp kế hoạch" (2070:3221): the step's id in Plan IR and who does it. */
  id?: string;
  agent_name?: string;
}

/** agent.agent_step, for "Xem kỹ thuật" (GET /v1/runs/{id}?view=technical). */
export interface AgentStep {
  seq: number;
  kind:
    | 'plan'
    | 'llm_call'
    | 'tool_call'
    | 'query'
    | 'skill'
    | 'agent_call'
    | 'checkpoint'
    | 'message';
  name: string;
  summary: string | null;
  status: 'ok' | 'error' | 'retried' | 'skipped' | 'simulated';
  /** null while the step is under way or not started. */
  duration_ms: number | null;
  /*
   * PROPOSAL, for the trace waterfall of "P13 · Lượt chạy · Kỹ thuật" (2310:20156): the step it
   * runs inside (indent), when it started (its place on the time axis), what ran it ("Qwen3-72B",
   * "SQL", "kỹ năng"), one figure ("1.2k tok", "214 dòng"), and for the detail panel whose
   * permissions it ran under, what it read and the query it sent.
   */
  parent_seq: number | null;
  started_at: Timestamp | null;
  actor: string;
  metric_text: string | null;
  run_as_text: string | null;
  rows_text: string | null;
  sql: string | null;
}

/** PROPOSAL: run fields the screens need and the api does not send yet. */
interface RunDisplay {
  /** Joined agent name, step count and ETA for the progress rows. */
  agent_name: string;
  total_steps: number;
  eta_text: string;
  /** For "Lượt chạy": a short code, the goal in words, who or what started it, HITL count. */
  code: string;
  title: string;
  initiated_label: string;
  trigger_text: string;
  hitl_count: number;
  /** The plan beside the story. */
  plan: RunPlanStep[];
  /** qa.plan.version of `plan`; editing it saves version + 1. */
  plan_version: number;
  /**
   * For "v3.1 · Trạng thái · Lượt chạy lỗi" (2076:5030): a failed run's error in words and as
   * the system gave it, what the agent did to stay safe, and the fallback source it can rerun
   * against (null when there is none).
   */
  failure: { title: string; detail: string; safety: string[]; fallback_source: string | null };
}

type RunView = components['schemas']['RunView'];

/**
 * GET /v1/runs (RunSummary) and GET /v1/runs/{id} (RunView) as the api sends them, plus the
 * PROPOSAL fields when present (the mock sends them).
 */
export type RunWire = components['schemas']['RunSummary'] &
  Partial<Omit<RunView, keyof components['schemas']['RunSummary'] | 'steps'>> & {
    steps?: (components['schemas']['StepView'] | AgentStep)[] | null;
  } & Partial<RunDisplay>;

/** A run as the screens read it; `readRun` (lib/v1/api.ts) leaves a missing PROPOSAL field null. */
export type AgentRun = Omit<
  RunWire,
  'status' | 'trigger_kind' | 'narrative' | 'steps' | 'error' | keyof RunDisplay
> & {
  status: RunStatus;
  trigger_kind: 'schedule' | 'event' | 'threshold' | 'agent_call' | 'manual' | 'test';
  /** "Diễn biến kể thành câu cho màn Đang làm gì": the entries that read as a sentence. */
  narrative: NarrativeEvent[];
  error: string | null;
  /** The agent's name; "Agent" when it cannot be joined. */
  agent_name: string;
  total_steps: number | null;
  eta_text: string | null;
  /** A short code; the start of the id without the PROPOSAL field. */
  code: string;
  /** The goal in words; "Lượt chạy của <agent>" without the PROPOSAL field. */
  title: string;
  initiated_label: string | null;
  trigger_text: string | null;
  hitl_count: number | null;
  plan?: RunPlanStep[];
  plan_version?: number;
  failure?: RunDisplay['failure'];
  /** view=technical only. */
  steps?: AgentStep[];
};

/* ─── Phân tích: workspace.workspace ──────────────────────────────────── */

/** PROPOSAL: people and agents in a workspace, and whether an agent waits on the viewer. */
interface WorkspaceActivity {
  participants: { people: number; agents: number };
  agent_state: 'waiting_you' | 'running' | null;
}

/** GET /v1/workspaces as the api sends it, plus the PROPOSAL activity when present. */
export type WorkspaceWire = components['schemas']['WorkspaceView'] & Partial<WorkspaceActivity>;

/** A workspace as the session lists read it; without the PROPOSAL fields they are left out. */
export type Workspace = Omit<WorkspaceWire, 'created_from' | 'status' | keyof WorkspaceActivity> & {
  created_from: 'blank' | 'conversation' | 'template' | 'agent_run';
  status: 'active' | 'archived';
  participants: WorkspaceActivity['participants'] | null;
  agent_state: WorkspaceActivity['agent_state'];
};

export const readWorkspace = (raw: WorkspaceWire): Workspace => ({
  ...raw,
  created_from: raw.created_from as Workspace['created_from'],
  status: raw.status as Workspace['status'],
  participants: raw.participants ?? null,
  agent_state: raw.agent_state ?? null,
});

/* ─── Dữ liệu: catalog.source, catalog.dataset, context.metric ────────── */

export type SourceStatus = 'draft' | 'testing' | 'active' | 'needs_fix' | 'paused' | 'archived';

/** PROPOSAL: freshness the source lists draw; the api does not send it yet. */
interface SourceFreshness {
  /** Last successful refresh, and how late it is against freshness_sla. */
  last_refreshed_at: Timestamp | null;
  late_by_minutes: number | null;
}

/** GET /v1/sources as the api sends it (catalog SourceView), plus the PROPOSAL freshness. */
export type SourceWire =
  components['schemas']['services__api__modules__catalog__schemas__SourceView'] &
    Partial<SourceFreshness>;

export type Source = Omit<SourceWire, 'mode' | 'status' | keyof SourceFreshness> & {
  mode: 'federated' | 'ingest';
  status: SourceStatus;
  last_refreshed_at: Timestamp | null;
  late_by_minutes: number | null;
};

export const readSource = (raw: SourceWire): Source => ({
  ...raw,
  mode: raw.mode as Source['mode'],
  status: raw.status as SourceStatus,
  last_refreshed_at: raw.last_refreshed_at ?? null,
  late_by_minutes: raw.late_by_minutes ?? null,
});

/** GET /v1/datasets as the api sends it. PROPOSAL: `certified`, for "Bộ dữ liệu đã chứng nhận". */
export type DatasetWire =
  components['schemas']['services__api__modules__catalog__schemas__DatasetView'] & {
    certified?: boolean;
  };

export type Dataset = Omit<DatasetWire, 'display_name' | 'certified'> & {
  /** The display name, or the table's name without one. */
  display_name: string;
  /** null when the api does not say (the certification is on the dataset version). */
  certified: boolean | null;
};

export const readDataset = (raw: DatasetWire): Dataset => ({
  ...raw,
  display_name: raw.display_name ?? raw.table_name,
  certified: raw.certified ?? null,
});

/** GET /v1/metrics (context MetricView). */
export type Metric = components['schemas']['MetricView'];

/** GET /v1/terms (context TermView). */
export type GlossaryTerm = components['schemas']['TermView'];

/* ─── Quản trị: org.data_space, org.app_user, governance.model_config ── */

/** GET /v1/data-spaces (A3 · Quản trị), generated: a space the caller is a member of, with the
 * caller's roles there and the capabilities they add to the organisation-wide ones (GET /v1/me). */
export type DataSpace = components['schemas']['DataSpaceSummary'];

export interface AppUser {
  id: Uuid;
  display_name: string;
  kind: 'person' | 'agent';
  status: string;
}

export interface ModelConfig {
  task: string;
  model_name: string;
  status: string;
  /** "Kèm độ trễ p95 và kết quả bộ đánh giá gần nhất". PROPOSAL: score delta vs previous run. */
  latest_eval_delta: number | null;
}

/* ─── Chính sách: governance.behavior_policy ──────────────────────────── */

export interface BehaviorPolicy {
  id: Uuid;
  scope: 'system' | 'org' | 'data_space' | 'agent' | 'user';
  kind: 'block' | 'mask' | 'require_approval' | 'auto_handle' | 'allow' | 'budget';
  /** "Mô tả bằng lời hiện trên giao diện" */
  description: string;
  origin: 'manual' | 'hitl' | 'pack';
  status: 'draft' | 'active' | 'disabled' | 'expired';
  /** PROPOSAL: how often the rule handled a request instead of asking, this week. */
  applied_this_week: number;
}

/* ─── Duyệt đề xuất: inbox.proposal (GET /v1/proposals/{id}) ──────────── */

export type ProposalStatus =
  | 'open'
  | 'evaluating'
  | 'pending_review'
  | 'accepted'
  | 'applied'
  | 'apply_failed'
  | 'rejected'
  | 'expired'
  | 'withdrawn';

/** PROPOSAL: the shape of `diff` for a definition change — the two versions side by side. */
export interface ProposalDiff {
  before: { label: string; text: string };
  after: { label: string; text: string };
}

/**
 * "Kèm diff, ảnh hưởng, kết quả eval_run". PROPOSAL: the shape of `impact` — a backtest table
 * over past periods and the consequence in one sentence.
 */
export interface ProposalImpact {
  window_label: string;
  columns: [string, string];
  rows: { period: string; before: string; after: string; delta: string }[];
  summary: string;
}

/** PROPOSAL: display fields the api does not send yet (docs/v3/05-ui-v3.md §4); the mock does. */
export interface ProposalDisplay {
  /** eval_run results in words, each passed or flagged. */
  checks: { ok: boolean; text: string }[];
  /** The approval chain (proposal_review steps) and why it has that many steps. */
  approval: { reason: string; steps: { name: string; role: string }[]; note: string };
  /** Who proposed it and from where, "Agent Phân tích từ phiên “Nợ xấu bán lẻ T9”". */
  proposed_by_label: string;
  /** A short code for the breadcrumb, "PR-0318". */
  code: string;
}

/** GET /v1/proposals/{id} as the api sends it, plus the PROPOSAL fields when present. */
export type ProposalWire = components['schemas']['ProposalDetail'] & Partial<ProposalDisplay>;

/**
 * A proposal as "P07 · Duyệt đề xuất" reads it; `readProposal` (lib/v1/api.ts) keeps `diff` and
 * `impact` only when they have the drawn shape and derives `checks` from eval_run without them.
 */
export type Proposal = Omit<
  ProposalWire,
  'diff' | 'impact' | 'status' | 'impact_level' | keyof ProposalDisplay
> & {
  status: ProposalStatus;
  impact_level: 'low' | 'medium' | 'high';
  diff: ProposalDiff | null;
  /** The column as sent, shown as is when it does not have the side-by-side shape. */
  raw_diff: unknown;
  impact: ProposalImpact | null;
  checks: ProposalDisplay['checks'];
  approval: ProposalDisplay['approval'] | null;
  proposed_by_label: string | null;
  code: string | null;
};

/** POST /v1/proposals/{id}:decide — `note` is required to reject (proposal_review). */
export type DecideProposal = RequestBody<
  components['schemas']['ProposalDecision'],
  'reviewed_regressions'
>;

/* ─── So sánh hai nguồn: context of a `conflict` HITL request ─────────── */

/** PROPOSAL: what a conflict request carries in `context` for "Cần bạn · So sánh hai nguồn". */
// A type alias, not an interface, so it fits HitlRequest.context (an open record).
export type ConflictContext = {
  why?: string;
  subtitle: string;
  sources: {
    name: string;
    authority: string;
    authority_tone: 'success' | 'info';
    detail: string;
    value: string;
    value_label: string;
    query: string;
  }[];
  gap: { headline: string; detail: string };
  checks: { mark: 'ok' | 'warn' | 'unknown'; text: string }[];
  impact_title: string;
  impact: { title: string; detail: string }[];
  authority_note: string;
  similar_note: string;
  rule_suggestion: string | null;
};
