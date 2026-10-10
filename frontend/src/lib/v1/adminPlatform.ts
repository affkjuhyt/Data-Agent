import { pathId, v1Get, v1Send, type Page } from './client';
import type { ModelConfig } from './types';

/*
 * Platform side of Quản trị and the personal settings (UC-11-04 giám sát, UC-11-05 mô hình AI,
 * UC-12-01..04 tích hợp, UC-13-06 vận hành agent, UC-11-01 hồ sơ, UC-08-07 thông báo).
 *
 * The system design names admin.model_config and the user's notification channels, but has no
 * read model for service health, GPU load, client usage, the ops roll-up or the channel rules
 * matrix. Everything marked PROPOSAL is asked of the backend; the mock (mocks/adminPlatform.ts)
 * serves it. Counts and amounts arrive computed: the screens never derive totals from a page.
 */
type Uuid = string;
type Timestamp = string;
type Tone = 'success' | 'warning' | 'error' | 'idle';

/* ─── Giám sát hệ thống (UC-11-04) ─────────────────────────────────────── */

/** PROPOSAL: GET /v1/admin/system-health, one snapshot of the platform. */
export interface SystemHealth {
  as_of: Timestamp;
  services_up: number;
  services_total: number;
  /** The one event worth a line under the count: "Ingestion khởi động lại 07:42". */
  services_note: string | null;
  latency_p95_ms: number;
  latency_target_ms: number;
  gpu_utilization: number;
  gpu_hosts: number;
  gpu_cards: number;
  error_rate_24h: number;
  /** Change of the 24 h error rate vs the day before, in percentage points. */
  error_rate_delta_pp: number;
  services: ServiceHealth[];
  models: ModelLoad[];
  alerts: SystemAlert[];
  /** "PostgreSQL sao lưu 02:00 hôm nay · sao chép sang site DR trễ 40 giây." */
  backup_text: string;
}

export interface ServiceHealth {
  name: string;
  status: 'ok' | 'degraded' | 'restarted' | 'down';
  version: string;
  latency_p95_ms: number | null;
  error_rate_24h: number;
  note: string | null;
}

export interface ModelLoad {
  name: string;
  tasks_text: string;
  tokens_today: number | null;
  queue: number;
  /** "12 trang": the queue of a document model counts pages, not requests. */
  queue_unit: 'request' | 'page';
  gpu_cards: number;
  gpu_utilization: number;
}

export interface SystemAlert {
  id: string;
  severity: 'warning' | 'info' | 'error';
  title: string;
  detail: string;
}

export const getSystemHealth = () => v1Get<SystemHealth>('/admin/system-health');

/* ─── Vận hành agent (UC-13-06, UC-11-04) ──────────────────────────────── */

export type OpsPeriod = '24h' | '7d' | '30d';

/** PROPOSAL: GET /v1/agent-ops/summary?period=, the roll-up of every agent's runs. */
export interface AgentOpsSummary {
  period: OpsPeriod;
  /** Plain-language summary written by the backend from the numbers below. */
  headline: string;
  runs: number;
  runs_change_ratio: number;
  auto_rate: number;
  auto_rate_change_pp: number;
  hitl_requests: number;
  hitl_median_response_s: number;
  errors: number;
  error_rate: number;
  error_note: string | null;
  cost: string;
  budget_used_ratio: number;
  /** One bar per day (24h: per hour block), oldest first. */
  series: { label: string; auto: number; human: number; error: number }[];
  agents: AgentOpsRow[];
  issues: OpsIssue[];
}

export interface AgentOpsRow {
  agent_id: Uuid;
  name: string;
  runs: number;
  auto_rate: number;
  hitl_requests: number;
  avg_duration_s: number;
  cost: string;
  eval_score: number | null;
  /** Negative when the last eval run scored lower than the one before. */
  eval_change_pp: number | null;
}

export interface OpsIssue {
  id: string;
  severity: 'error' | 'warning' | 'info';
  title: string;
  detail: string;
  /** What the action opens: a run trace, the agent's eval comparison, or its budget. */
  action: { kind: 'run' | 'eval' | 'budget'; label: string; target_id: Uuid };
}

export const getAgentOpsSummary = (period: OpsPeriod) =>
  v1Get<AgentOpsSummary>('/agent-ops/summary', { period });

/* ─── Mô hình & chính sách AI (UC-11-05) ───────────────────────────────── */

/** GET /v1/admin/models row with the fields the P11 screen needs. */
export interface ModelRoute extends ModelConfig {
  /** PROPOSAL: stable key of the task, for the rollback path. */
  id: string;
  /** PROPOSAL: where it runs, "GPU cụm A"; empty when it is not pinned. */
  placement: string | null;
  /** PROPOSAL: model used when this one is overloaded; "luật" for the rule-based fallback. */
  fallback_name: string | null;
  /** PROPOSAL */
  latency_p95_ms: number;
  /** PROPOSAL: last eval score in [0,1], null when the task has no eval set. */
  eval_score: number | null;
  /** PROPOSAL: the model before the last change, and its cost relative to the current one. */
  previous: { model_name: string; cost_ratio: number } | null;
  /** PROPOSAL: whose eval set reruns when this task changes model, "Agent Báo cáo". */
  eval_suite_name: string | null;
}

/** PROPOSAL: GET /v1/admin/ai-policy, the org-wide guardrails in the words shown to admins. */
export interface AiPolicy {
  rules: { key: string; label: string; value_text: string }[];
}

/** PROPOSAL: GET /v1/admin/gpu-load?window=24h. */
export interface GpuLoad {
  points: { at: Timestamp; utilization: number }[];
  peak: { at: Timestamp; utilization: number };
  /** "Khi vượt 90% trong 10 phút, tác vụ sinh SQL chuyển sang mô hình dự phòng." */
  failover_text: string;
}

export interface NewModel {
  name: string;
  endpoint: string;
  trial_tasks: string;
  rate_limit_per_min: number;
}

export const listModelRoutes = () => v1Get<Page<ModelRoute>>('/admin/models', { page_size: 500 });
export const getAiPolicy = () => v1Get<AiPolicy>('/admin/ai-policy');
export const getGpuLoad = () => v1Get<GpuLoad>('/admin/gpu-load', { window: '24h' });

/** PROPOSAL: registers the model and starts its eval run; it is routed only after it passes. */
export const addModel = (body: NewModel) => v1Send<ModelRoute>('POST', '/admin/models', body);

/** PROPOSAL: reruns the eval sets of every routed task. */
export const runModelEvals = () =>
  v1Send<{ eval_run_id: string }>('POST', '/admin/models:evaluate');

export const rollbackModel = async (id: string) =>
  v1Send<ModelRoute>('POST', `/admin/models/${pathId(id)}:rollback`);

/* ─── Tích hợp: client API, MCP, webhook (UC-12-01..04) ────────────────── */

export type IntegrationKind = 'mcp' | 'api' | 'webhook';

/** PROPOSAL: GET /v1/admin/integrations. A client acts only as the user who calls through it. */
export interface Integration {
  id: Uuid;
  name: string;
  kind: IntegrationKind;
  owner_name: string;
  scopes: string[];
  calls_7d: number;
  error_rate_7d: number | null;
  expires_at: Timestamp;
  status: 'active' | 'expiring' | 'expired' | 'revoked';
}

/** PROPOSAL: GET /v1/admin/integrations/{id}/usage?days=7. */
export interface IntegrationUsage {
  daily_calls: number[];
  top_tools: { name: string; share: number }[];
  quota_per_day: number;
  quota_used_ratio: number;
  blocked_by_permission: number;
}

export interface NewIntegration {
  kind: IntegrationKind;
  endpoint: string;
  auth: 'oauth2_client_credentials' | 'api_key' | 'mtls';
  tools: string[];
  /** null = every agent of the organisation. */
  data_space_id: string | null;
}

export const listIntegrations = () =>
  v1Get<Page<Integration>>('/admin/integrations', { page_size: 500 });

export const getIntegrationUsage = async (id: string) =>
  v1Get<IntegrationUsage>(`/admin/integrations/${pathId(id)}/usage`, { days: 7 });

/** PROPOSAL: connects, calls the endpoint once to check it, and answers 422 when that fails. */
export const addIntegration = (body: NewIntegration) =>
  v1Send<Integration>('POST', '/admin/integrations', body);

/* ─── Cài đặt cá nhân (UC-11-01, UC-08-07) ─────────────────────────────── */

/** GET /v1/me. Identity comes from Active Directory; only the display format is the user's. */
export interface Me {
  display_name: string;
  email: string;
  /** PROPOSAL: "Khối Quản lý rủi ro · Phòng Rủi ro tín dụng" */
  unit_text: string;
  /** PROPOSAL: "Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)" */
  role_text: string;
  data_space_names: string[];
  locale_label: string;
  /** PROPOSAL: "1.234,56 · ngày dd/mm/yyyy · giờ 24h" */
  format_text: string;
  timezone: string;
}

export const getMe = () => v1Get<Me>('/me');

export type NotificationChannel = 'in_app' | 'teams' | 'zalo' | 'email' | 'digest';

/** PROPOSAL: GET/PUT /v1/me/notification-preferences. */
export interface NotificationPreferences {
  events: {
    key: string;
    label: string;
    description: string;
    channels: Record<NotificationChannel, boolean>;
    /** Channels the user cannot turn off (high-risk work always reaches the app). */
    locked: NotificationChannel[];
  }[];
  quiet_hours_text: string;
  grouping_text: string;
}

export const getNotificationPreferences = () =>
  v1Get<NotificationPreferences>('/me/notification-preferences');

export const saveNotificationPreferences = (body: NotificationPreferences) =>
  v1Send<NotificationPreferences>('PUT', '/me/notification-preferences', body);

/** PROPOSAL: sends one sample notification to every channel the user has turned on. */
export const sendTestNotification = () =>
  v1Send<{ channels: NotificationChannel[] }>('POST', '/me/notification-preferences:test');

/** PROPOSAL: GET /v1/channel-rules, what each outside channel may do per kind of request. */
export interface ChannelRules {
  channels: { key: string; label: string }[];
  rows: {
    kind: string;
    label: string;
    cells: Record<string, { text: string; tone: Tone }>;
    note: string;
  }[];
  principles: { title: string; body: string }[];
}

export const getChannelRules = () => v1Get<ChannelRules>('/channel-rules');
