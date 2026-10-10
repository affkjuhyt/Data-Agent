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

export interface HitlRequest {
  id: Uuid;
  data_space_id: Uuid;
  kind: HitlKind;
  risk: 'low' | 'medium' | 'high';
  title: string;
  /** "Dữ liệu để người quyết định". PROPOSAL: `why` carries the "Vì sao agent hỏi" sentence. */
  context: { why?: string } & Record<string, unknown>;
  options: HitlOption[];
  source_service: string;
  source_ref: Uuid | null;
  assignee_id: Uuid | null;
  due_at: Timestamp | null;
  status: HitlStatus;
  created_at: Timestamp;
  resolved_at: Timestamp | null;
  /** PROPOSAL: the agent that asked, for the AgentChip, joined from source_ref. */
  agent: { id: Uuid; name: string } | null;
}

/** POST /v1/hitl-requests/{id}:resolve */
export interface ResolveHitl {
  decision: 'answer' | 'approve' | 'reject' | 'reassign';
  option?: string;
  note?: string;
  reassign_to?: Uuid;
  remember_as_rule?: boolean;
}

/* ─── Agents: agent.agent_def, agent.agent_run ────────────────────────── */

export type AgentStatusV1 = 'draft' | 'testing' | 'running' | 'paused' | 'retired';

export interface AgentDef {
  id: Uuid;
  data_space_id: Uuid;
  code: string;
  name: string;
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
  paused_reason: string | null;
  monthly_budget: string | null;
  spent_this_month: string;
  /** GET /v1/agents: "Kèm số lượt chạy đang chạy, chờ người, chi phí tháng". */
  runs_running: number;
  runs_waiting_human: number;
  /** PROPOSAL, for the "Đội agent" table: the goal of the running config version. */
  goal: string;
  /** PROPOSAL: what it is doing now, one sentence, and its tone. */
  now: { text: string; tone: 'normal' | 'waiting' | 'error' };
  /** PROPOSAL: the triggers in words, "Khi khoá sổ · thứ Hai 07:00". */
  trigger_text: string;
  /** PROPOSAL: what it may do, "Đọc · đề xuất". */
  permission_text: string;
  /** PROPOSAL: last 7 days, "12 lượt · 58% cần duyệt". */
  runs_7d: { count: number; needs_approval_pct: number; errors: number };
}

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

export interface AgentRun {
  id: Uuid;
  agent_id: Uuid;
  config_version: number;
  status: RunStatus;
  trigger_kind: 'schedule' | 'event' | 'threshold' | 'agent_call' | 'manual' | 'test';
  current_step: number | null;
  /** "Diễn biến kể thành câu cho màn Đang làm gì". */
  narrative: NarrativeEvent[];
  cost: string;
  waiting_since: Timestamp | null;
  started_at: Timestamp | null;
  finished_at: Timestamp | null;
  error: string | null;
  stop_reason: string | null;
  /** PROPOSAL: joined agent name, step count and ETA for the progress rows. */
  agent_name: string;
  total_steps: number | null;
  eta_text: string | null;
  /** PROPOSAL, for "Lượt chạy": a short code, the goal in words, who or what started it, HITL count. */
  code: string;
  title: string;
  initiated_label: string;
  trigger_text: string;
  hitl_count: number;
  /** PROPOSAL: the plan beside the story, on GET /v1/runs/{id}. */
  plan?: RunPlanStep[];
  /** PROPOSAL: qa.plan.version of `plan`; editing it saves version + 1. */
  plan_version?: number;
  /**
   * PROPOSAL, for "v3.1 · Trạng thái · Lượt chạy lỗi" (2076:5030): a failed run's error in words
   * and as the system gave it, what the agent did to stay safe, and the fallback source it can
   * rerun against (null when there is none).
   */
  failure?: {
    title: string;
    detail: string;
    safety: string[];
    fallback_source: string | null;
  };
  /** view=technical only. */
  steps?: AgentStep[];
}

/* ─── Phân tích: workspace.workspace ──────────────────────────────────── */

export interface Workspace {
  id: Uuid;
  title: string;
  description: string | null;
  created_from: 'blank' | 'conversation' | 'template' | 'agent_run';
  tile_count: number;
  last_refreshed_at: Timestamp | null;
  status: 'active' | 'archived';
  updated_at: Timestamp;
  /** PROPOSAL: people and agents in it, and whether an agent waits on the viewer. */
  participants: { people: number; agents: number };
  agent_state: 'waiting_you' | 'running' | null;
}

/* ─── Dữ liệu: catalog.source, catalog.dataset, context.metric ────────── */

export type SourceStatus = 'draft' | 'testing' | 'active' | 'needs_fix' | 'paused' | 'archived';

export interface Source {
  id: Uuid;
  name: string;
  kind: string;
  mode: 'federated' | 'ingest';
  status: SourceStatus;
  status_reason: string | null;
  last_checked_at: Timestamp | null;
  /** PROPOSAL: last successful refresh and whether it is late against freshness_sla. */
  last_refreshed_at: Timestamp | null;
  late_by_minutes: number | null;
}

export interface Dataset {
  id: Uuid;
  display_name: string;
  enabled: boolean;
  /** PROPOSAL: certified for use in Khám phá / dashboards ("Bộ dữ liệu đã chứng nhận"). */
  certified: boolean;
}

export interface Metric {
  id: Uuid;
  code: string;
  status: 'draft' | 'active' | 'retired';
  current_version: number | null;
}

export interface GlossaryTerm {
  id: Uuid;
  term: string;
}

/* ─── Quản trị: org.data_space, org.app_user, governance.model_config ── */

export interface DataSpace {
  id: Uuid;
  code: string;
  name: string;
  status: 'draft' | 'onboarding' | 'active' | 'read_only' | 'archived';
  /** "kèm vai trò của người gọi" */
  my_roles: string[];
}

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

export interface Proposal {
  id: Uuid;
  kind: string;
  title: string;
  /** PROPOSAL: the shape of `diff` for a definition change — the two versions side by side. */
  diff: { before: { label: string; text: string }; after: { label: string; text: string } };
  reason: string | null;
  /**
   * "Kèm diff, ảnh hưởng, kết quả eval_run". PROPOSAL: the shape — a backtest table over past
   * periods and the consequence in one sentence.
   */
  impact: {
    window_label: string;
    columns: [string, string];
    rows: { period: string; before: string; after: string; delta: string }[];
    summary: string;
  };
  impact_level: 'low' | 'medium' | 'high';
  status: ProposalStatus;
  expires_at: Timestamp;
  requires_compliance: boolean;
  /** PROPOSAL: eval_run results in words, each passed or flagged. */
  checks: { ok: boolean; text: string }[];
  /** PROPOSAL: the approval chain (proposal_review steps) and why it has that many steps. */
  approval: { reason: string; steps: { name: string; role: string }[]; note: string };
  /** PROPOSAL: who proposed it and from where, "Agent Phân tích từ phiên “Nợ xấu bán lẻ T9”". */
  proposed_by_label: string;
  /** PROPOSAL: a short code for the breadcrumb, "PR-0318". */
  code: string;
}

/** POST /v1/proposals/{id}:decide — proposal_review.decision; `note` is required to reject. */
export interface DecideProposal {
  decision: 'accept' | 'accept_with_edit' | 'reject' | 'request_changes';
  note?: string;
  reviewed_regressions?: boolean;
}

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
