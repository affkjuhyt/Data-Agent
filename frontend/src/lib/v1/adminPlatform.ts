import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';
import type { ModelConfig } from './types';
import { listAdminUsers } from './adminAccess';

type Schemas = components['schemas'];

/*
 * Platform side of Quản trị and the personal settings (UC-11-04 giám sát, UC-11-05 mô hình AI,
 * UC-12-01..04 tích hợp, UC-13-06 vận hành agent, UC-11-01 hồ sơ, UC-08-07 thông báo).
 *
 * The system design names admin.model_config and the user's notification channels, but has no
 * read model for service health, GPU load, client usage, the ops roll-up or the channel rules
 * matrix. Everything marked PROPOSAL is asked of the backend; the mock (mocks/adminPlatform.ts)
 * serves it. Counts and amounts arrive computed: the screens never derive totals from a page.
 * Where A2 has the resource (models, model providers, route decisions, notification prefs) its
 * shapes are read below into the screens' ones.
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
  /** p95 latency from the gateway; null before it has served calls. */
  latency_p95_ms: number | null;
  /** PROPOSAL: last eval score in [0,1], null when the task has no eval set. */
  eval_score: number | null;
  /** PROPOSAL: the model before the last change, and its cost relative to the current one. */
  previous: { model_name: string; cost_ratio: number } | null;
  /** PROPOSAL: whose eval set reruns when this task changes model, "Agent Báo cáo". */
  eval_suite_name: string | null;
}

/** PROPOSAL: GET/PUT /v1/admin/ai-policy, the org-wide guardrails in the words shown to admins. */
export interface AiPolicy {
  rules: { key: string; label: string; value_text: string }[];
  /**
   * PROPOSAL: the org switch for frontier providers (UC-11-05). Off = every call runs on the
   * local models; on = calls whose whole context is labelled Công khai may go to a frontier
   * provider approved by AC06. The server refuses `true` (409) while none is approved.
   */
  frontier_allowed: boolean;
  /** PROPOSAL: the sensitivity routing rules, in order, as the policy guard applies them. */
  routing_rules: string[];
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

type ModelWire = Schemas['ModelConfigView'] &
  Partial<
    Pick<ModelRoute, 'placement' | 'fallback_name' | 'eval_score' | 'previous' | 'eval_suite_name'>
  > & { latest_eval_delta?: number | null };

/** latest_eval is the eval run's row: its pass rate is the score. */
const scoreOf = (raw: unknown): number | null => {
  if (typeof raw !== 'object' || raw === null) return null;
  const { pass_rate } = raw as { pass_rate?: unknown };
  const n = typeof pass_rate === 'string' ? Number(pass_rate) : pass_rate;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
};

const readModelRoute = (raw: ModelWire): ModelRoute => ({
  id: raw.id,
  task: raw.task,
  model_name: raw.model_name,
  status: raw.status,
  latest_eval_delta: raw.latest_eval_delta ?? null,
  placement: raw.placement ?? null,
  fallback_name: raw.fallback_name ?? raw.fallback_model,
  latency_p95_ms: raw.latency_p95_ms ?? null,
  eval_score: raw.eval_score ?? scoreOf(raw.latest_eval),
  previous: raw.previous ?? null,
  eval_suite_name: raw.eval_suite_name ?? null,
});

/** GET /v1/admin/models answers the whole list, not a page. */
export const listModelRoutes = async (): Promise<Page<ModelRoute>> => {
  const items = await v1Get<ModelWire[]>('/admin/models');
  return { items: items.map(readModelRoute), next_page_token: '' };
};
export const getAiPolicy = () => v1Get<AiPolicy>('/admin/ai-policy');
export const getGpuLoad = () => v1Get<GpuLoad>('/admin/gpu-load', { window: '24h' });

/** PROPOSAL: registers the model and starts its eval run; it is routed only after it passes. */
export const addModel = (body: NewModel) => v1Send<ModelRoute>('POST', '/admin/models', body);

/** PROPOSAL: reruns the eval sets of every routed task. */
export const runModelEvals = () =>
  v1Send<{ eval_run_id: string }>('POST', '/admin/models:evaluate');

export const rollbackModel = async (id: string) =>
  v1Send<ModelRoute>('POST', `/admin/models/${pathId(id)}:rollback`);

/** PROPOSAL: turning frontier on or off is a policy change; it is audited server-side. */
export const saveAiPolicy = (body: AiPolicy) => v1Send<AiPolicy>('PUT', '/admin/ai-policy', body);

/* ─── Provider và định tuyến theo độ nhạy (UC-11-05, BR-180, BR-181) ───── */

/** PROPOSAL: GET /v1/admin/providers, where the models run and whether data may go there. */
export interface AiProvider {
  id: string;
  name: string;
  /** "local" runs inside the bank; "frontier" is an outside API. */
  kind: 'local' | 'frontier';
  model_name: string;
  /** Second line of the model cell: the secondary model, or how the provider may be used. */
  model_note: string | null;
  endpoint_label: string;
  /** "llm.noibo.local · trong ngân hàng", "Vùng Singapore". */
  region_text: string;
  /** Zero data retention commitment; not applicable to a local provider. */
  zero_retention: 'not_applicable' | 'committed' | 'verifying' | 'none';
  /** Approval by the AC06 committee; a frontier provider is routed to only once approved. */
  ac06_status: 'approved' | 'pending' | 'rejected';
  /** Month-to-date spend and monthly budget in VND; both null for local (GPU hours). */
  month_cost_vnd: number | null;
  month_budget_vnd: number | null;
  /** "2026-10": the month the spend counts. */
  budget_month: string;
}

export interface NewProvider {
  name: string;
  kind: 'local' | 'frontier';
  model_name: string;
  endpoint: string;
  region: string;
  /** null for a local provider. */
  month_budget_vnd: number | null;
}

/** PROPOSAL: one trial call through the provider. */
export interface ProviderTest {
  ok: boolean;
  latency_ms: number | null;
  /** Why it failed, in words for the admin. */
  detail: string | null;
}

export type RoutingDecision = 'frontier' | 'blocked' | 'local';

/** PROPOSAL: one row of the routing audit (BR-181): decision, reason, provider, asker. */
export interface RoutingLogEntry {
  id: string;
  at: Timestamp;
  question: string;
  decision: RoutingDecision;
  reason: string;
  provider_name: string;
  asked_by_name: string;
}

/** PROPOSAL: GET /v1/admin/routing-log?week=, a cursor page plus the week's counts. */
export interface RoutingLog extends Page<RoutingLogEntry> {
  /** ISO week, "2026-W41". */
  week: string;
  frontier_calls: number;
  blocked_internal: number;
  local_calls: number;
}

/** PROPOSAL: the row as the screen draws it, which the mock sends under `display`. */
export type ProviderWire = Schemas['ModelProviderView'] & { display?: AiProvider };

/** "2026-10" */
const thisMonth = () => new Date().toISOString().slice(0, 7);

/*
 * A provider's models are the keys of its price list (price per million tokens by model). An
 * on-prem provider is the organisation's own and needs no approval; a frontier one is routed to
 * only once the AC06 committee approved it.
 */
function readProvider(raw: ProviderWire): AiProvider {
  if (raw.display) return raw.display;
  const models = typeof raw.price === 'object' && raw.price !== null ? Object.keys(raw.price) : [];
  const local = raw.kind === 'on_prem';
  return {
    id: raw.id,
    name: raw.display_name,
    kind: local ? 'local' : 'frontier',
    model_name: models[0] ?? '',
    model_note: models.length > 1 ? models.slice(1).join(', ') : null,
    endpoint_label: raw.endpoint,
    region_text: raw.data_region ?? (local ? 'trong tổ chức' : ''),
    zero_retention: local ? 'not_applicable' : raw.zero_retention ? 'committed' : 'none',
    ac06_status:
      local || raw.approved_at ? 'approved' : raw.status === 'retired' ? 'rejected' : 'pending',
    month_cost_vnd: local ? null : Number(raw.monthly_cost_vnd ?? 0),
    month_budget_vnd:
      local || raw.monthly_budget_vnd === null ? null : Number(raw.monthly_budget_vnd),
    budget_month: thisMonth(),
  };
}

export const listProviders = async () => {
  const page = await v1Get<Page<ProviderWire>>('/admin/model-providers', { page_size: 500 });
  return { ...page, items: page.items.map(readProvider) };
};

/** "Nhà cung cấp A (API)" → "nha-cung-cap-a-api" (governance.model_provider.code). */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'provider';

/**
 * A new frontier provider starts pending AC06 and is not routed to until approved. The model is
 * the first key of its price list; the price itself is set by the admin later.
 */
export const addProvider = async (body: NewProvider) =>
  readProvider(
    await v1Send<ProviderWire>('POST', '/admin/model-providers', {
      code: codeOf(body.name),
      display_name: body.name,
      kind: body.kind === 'local' ? 'on_prem' : 'frontier',
      endpoint: body.endpoint,
      data_region: body.region || null,
      monthly_budget_vnd: body.month_budget_vnd,
      price: body.model_name ? { [body.model_name]: {} } : {},
    }),
  );

/** One trial call through the provider with its model (POST :test needs the model's name). */
export const testProvider = async (id: string, modelName: string): Promise<ProviderTest> => {
  if (!modelName)
    throw new V1Error(422, 'validation_failed', 'Nhà cung cấp chưa có mô hình để thử.');
  const result = await v1Send<Schemas['ModelProviderTestResult']>(
    'POST',
    `/admin/model-providers/${pathId(id)}:test`,
    { model_name: modelName } satisfies Schemas['ModelProviderTest'],
  );
  return {
    ok: result.outcome === 'ok',
    latency_ms: result.latency_ms,
    detail: result.error ?? null,
  };
};

/** PROPOSAL: a row's question and asker in words, and the week's totals; the mock sends them. */
type RoutingWire = Page<Schemas['ModelRouteDecisionView'] & { display?: RoutingLogEntry }> &
  Partial<Pick<RoutingLog, 'week' | 'frontier_calls' | 'blocked_internal' | 'local_calls'>>;

const DECISION: Record<string, RoutingDecision> = {
  frontier_allowed: 'frontier',
  frontier_blocked: 'blocked',
  on_prem_default: 'local',
};

/** Monday 00:00 UTC of an ISO week, "2026-W41". */
function weekStart(week: string): Date {
  const [year, w] = week.split('-W').map(Number);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (w - 1) * 7);
  return monday;
}

/** The ISO week of a day: the week holding its Thursday, counted from the week of 4 January. */
function isoWeek(at: Date): string {
  const thursday = new Date(Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate()));
  thursday.setUTCDate(thursday.getUTCDate() + 3 - ((thursday.getUTCDay() + 6) % 7));
  const year = thursday.getUTCFullYear();
  const week =
    1 + Math.round((thursday.getTime() - weekStart(`${year}-W01`).getTime()) / 604_800_000 - 3 / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

/**
 * GET /v1/admin/model-route-decisions for the week (from/to). The audit keeps the prompt's hash,
 * not the question (BR-181): the row shows the task instead. The week's counts are counted over
 * the page read, which holds the week up to page_size decisions.
 */
export const getRoutingLog = async (opts: {
  week?: string;
  page_size: number;
  page_token?: string;
}): Promise<RoutingLog> => {
  const week = opts.week ?? isoWeek(new Date());
  const from = weekStart(week);
  const to = new Date(from.getTime() + 604_800_000);
  const [page, providers] = await Promise.all([
    v1Get<RoutingWire>('/admin/model-route-decisions', {
      from: from.toISOString(),
      to: to.toISOString(),
      page_size: opts.page_size,
      page_token: opts.page_token,
    }),
    listProviders().catch(() => null),
  ]);
  const names = new Map<string, string>(providers?.items.map((p) => [p.id, p.name]) ?? []);
  const items = page.items.map(
    (d): RoutingLogEntry =>
      d.display ?? {
        id: d.id,
        at: d.decided_at,
        question: d.task,
        decision: DECISION[d.decision] ?? 'local',
        reason: d.reasons.join('; '),
        provider_name: names.get(d.chosen_provider_id) ?? d.model_name,
        asked_by_name: d.caller_kind,
      },
  );
  const count = (k: RoutingDecision) => items.filter((i) => i.decision === k).length;
  return {
    items,
    next_page_token: page.next_page_token,
    week: page.week ?? week,
    frontier_calls: page.frontier_calls ?? count('frontier'),
    blocked_internal: page.blocked_internal ?? count('blocked'),
    local_calls: page.local_calls ?? count('local'),
  };
};

/* ─── Tích hợp: client API, MCP, webhook (UC-12-01..04) ────────────────── */

/*
 * A2 serves the clients (GET /v1/admin/clients since 12/10/2026, never a secret or hash), their
 * writes and usage, and the owner's side (/v1/me/clients). There is no list of webhooks.
 */
export type IntegrationKind = 'mcp' | 'api';

/** A client acts only as its owner, who confirms one granted in their name (BR-48). */
export interface Integration {
  id: Uuid;
  name: string;
  kind: IntegrationKind;
  owner_name: string;
  /** The MCP tools it was granted. */
  scopes: string[];
  /** PROPOSAL: calls and error rate over 7 days; null when the row has no figures. */
  calls_7d: number | null;
  error_rate_7d: number | null;
  quota_per_day: number;
  expires_at: Timestamp;
  status: 'pending_owner' | 'active' | 'expiring' | 'paused' | 'expired' | 'revoked';
}

export interface IntegrationUsage {
  daily_calls: number[];
  top_tools: { name: string; share: number }[];
  quota_per_day: number;
  quota_used_ratio: number;
  blocked_by_permission: number;
}

/** A3's listed client; PROPOSAL: the owner's name and 7-day figures under `display` (mock only). */
export type ClientWire = Schemas['ApiClientListed'] & {
  display?: Pick<Integration, 'owner_name' | 'calls_7d' | 'error_rate_7d'>;
};

/** The MCP tools a client may be granted (integration/rules.py MCP_TOOLS). */
export const MCP_TOOLS = [
  'ask_question',
  'get_metric_definition',
  'search_metrics',
  'run_skill',
  'get_published_result',
  'search_documents',
] as const;

// An active client this close to its expiry is drawn as expiring (BR-144: at most 12 months).
const EXPIRING_DAYS = 30;

function readClient(raw: ClientWire, names: Map<string, string>, now = Date.now()): Integration {
  const left = (new Date(raw.expires_at).getTime() - now) / 86_400_000;
  const status: Integration['status'] =
    raw.status === 'active' && left <= EXPIRING_DAYS
      ? 'expiring'
      : ((['pending_owner', 'active', 'paused', 'expired', 'revoked'] as const).find(
          (s) => s === raw.status,
        ) ?? 'paused');
  return {
    id: raw.id,
    name: raw.name,
    kind: raw.kind === 'mcp' ? 'mcp' : 'api',
    owner_name: raw.display?.owner_name ?? names.get(raw.owner_user_id) ?? '',
    scopes: Array.isArray(raw.tools) ? raw.tools : [],
    calls_7d: raw.display?.calls_7d ?? null,
    error_rate_7d: raw.display?.error_rate_7d ?? null,
    quota_per_day: raw.quota_per_day,
    expires_at: raw.expires_at,
    status,
  };
}

const userNames = async () => {
  const users = await listAdminUsers().catch(() => null);
  return new Map<string, string>(users?.items.map((u) => [u.id, u.display_name]) ?? []);
};

/** GET /v1/admin/clients. The 7-day figures come from the usage panel, not the list. */
export const listIntegrations = async (): Promise<Page<Integration>> => {
  const [page, names] = await Promise.all([
    v1Get<Page<ClientWire>>('/admin/clients', { page_size: 500 }),
    userNames(),
  ]);
  return { ...page, items: page.items.map((c) => readClient(c, names)) };
};

const localDay = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

/*
 * GET /v1/admin/clients/{id}/usage over the last 7 local days by the hour (day buckets are
 * truncated in the server's zone), summed here into days and tools. Quota use is today's calls
 * against the client's quota_per_day.
 */
export const getIntegrationUsage = async (client: Integration): Promise<IntegrationUsage> => {
  const today = new Date();
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6 + i);
    return localDay(d);
  });
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
  const page = await v1Get<Page<Schemas['UsageRow']>>(`/admin/clients/${pathId(client.id)}/usage`, {
    from: from.toISOString(),
    to: today.toISOString(),
    granularity: 'hour',
  });
  const daily = days.map(() => 0);
  const byTool = new Map<string, number>();
  let blocked = 0;
  for (const row of page.items) {
    const index = days.indexOf(localDay(new Date(row.bucket)));
    if (index >= 0) daily[index] += row.calls;
    byTool.set(row.tool, (byTool.get(row.tool) ?? 0) + row.calls);
    blocked += row.blocked_by_permission;
  }
  const total = daily.reduce((a, b) => a + b, 0);
  return {
    daily_calls: daily,
    top_tools: [...byTool]
      .filter(([, calls]) => calls > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([name, calls]) => ({ name, share: calls / total })),
    quota_per_day: client.quota_per_day,
    quota_used_ratio: client.quota_per_day > 0 ? daily[6] / client.quota_per_day : 0,
    blocked_by_permission: blocked,
  };
};

export interface NewClient {
  name: string;
  kind: IntegrationKind;
  owner_user_id: Uuid;
  tools: string[];
  data_space_ids: Uuid[];
  /** YYYY-MM-DD, at most 12 months ahead (BR-144). */
  expires_on: string;
}

/**
 * POST /v1/admin/clients. The secret is in this answer only. A client granted to someone else
 * waits in pending_owner until that person confirms it (GET /v1/me/clients).
 */
export const addClient = async (body: NewClient) => {
  const [y, m, d] = body.expires_on.split('-').map(Number);
  const issued = await v1Send<Schemas['ApiClientIssued']>('POST', '/admin/clients', {
    name: body.name,
    kind: body.kind === 'mcp' ? 'mcp' : 'rest',
    owner_user_id: body.owner_user_id,
    tools: body.tools,
    data_space_ids: body.data_space_ids,
    // End of the chosen local day.
    expires_at: new Date(y, m - 1, d, 23, 59, 59).toISOString(),
    // The api's defaults, sent because the generated type makes defaulted fields required.
    identity_mode: 'on_behalf_of_user',
    quota_per_minute: 60,
    quota_per_day: 5000,
  } satisfies Schemas['ApiClientCreate']);
  // A new client has made no call yet.
  const client = readClient({ ...issued, calls_24h: 0, errors_24h: 0 }, await userNames());
  return { client, secret: issued.client_secret };
};

/** The new secret is in this answer only. */
export const rotateClientSecret = async (id: string) =>
  (await v1Send<Schemas['ApiClientIssued']>('POST', `/admin/clients/${pathId(id)}:rotate-secret`))
    .client_secret;

/** Emergency revoke: the client's webhooks pause with it. */
export const revokeClient = (id: string, reason: string) =>
  v1Send<Schemas['ApiClientView']>('POST', `/admin/clients/${pathId(id)}:revoke`, {
    reason,
  } satisfies Schemas['Revoke']);

/** POST /v1/webhooks for an active client; the signing key is in this answer only. */
export const addWebhook = (body: Schemas['WebhookCreate']) =>
  v1Send<Schemas['WebhookIssued']>('POST', '/webhooks', body);

/* The owner's side (UC-12-01, BR-48) */

export type MyClient = Schemas['ApiClientView'];

export const listMyClients = () => v1Get<Page<MyClient>>('/me/clients', { page_size: 500 });

/**
 * Consent is to the client as listed: If-Match carries its updated_at, so a client an admin
 * widened after the list was read answers 412 instead of being confirmed.
 */
export const confirmMyClient = (client: MyClient) =>
  v1Send<MyClient>('POST', `/me/clients/${pathId(client.id)}:confirm`, undefined, {
    ifMatch: `"${client.updated_at}"`,
  });

/* ─── Cài đặt cá nhân (UC-11-01, UC-08-07) ─────────────────────────────── */

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

/** notification.kind in the order and words of "Cài đặt thông báo". */
const KIND_LABEL: Record<string, { label: string; description: string }> = {
  hitl: { label: 'Cần bạn quyết', description: 'Việc chờ bạn duyệt, chọn hoặc trả lời' },
  alert: { label: 'Cảnh báo', description: 'Chỉ số vượt ngưỡng bạn theo dõi' },
  task_done: { label: 'Việc đã xong', description: 'Agent hoặc lượt chạy bạn giao đã xong' },
  report: { label: 'Báo cáo', description: 'Báo cáo định kỳ đã phát hành' },
  digest: { label: 'Bản tin', description: 'Bản tin tổng hợp theo lịch' },
  comment: { label: 'Bình luận', description: 'Ai đó nhắc bạn hoặc trả lời bạn' },
  share: { label: 'Chia sẻ', description: 'Phiên, báo cáo được chia sẻ với bạn' },
  system: { label: 'Hệ thống', description: 'Thông báo của quản trị' },
};

const CHANNELS: NotificationChannel[] = ['in_app', 'teams', 'zalo', 'email', 'digest'];
type PrefChannel = Schemas['PrefIn']['channels'][number];

/** A HITL request always reaches the app (BR-67: high-risk work is decided in the app). */
const LOCKED: Record<string, NotificationChannel[]> = { hitl: ['in_app'] };

const BATCH_TEXT: Record<string, string> = {
  instant: 'Gửi ngay từng thông báo',
  '15m': 'Gộp thông báo trong 15 phút',
  daily: 'Gộp vào bản tin hằng ngày',
};

/*
 * GET /v1/me/notification-prefs: one row per kind, with defaults filled in. "Bản tin" is the
 * daily batch mode, not a channel of its own; quiet hours and grouping are said from the first
 * row that has them.
 */
/** PROPOSAL: the screen's labels and locks, which the mock sends under `display`. */
type PrefsWire = Page<Schemas['PrefView']> & { display?: NotificationPreferences };

function readPrefs(page: PrefsWire): NotificationPreferences {
  if (page.display) return page.display;
  const rows = page.items;
  const quiet = rows.find((r) => r.quiet_hours)?.quiet_hours as {
    from?: string;
    to?: string;
  } | null;
  const batch = rows.find((r) => r.batch_mode !== 'instant')?.batch_mode ?? 'instant';
  return {
    events: rows.map((r) => ({
      key: r.kind,
      label: KIND_LABEL[r.kind]?.label ?? r.kind,
      description: KIND_LABEL[r.kind]?.description ?? '',
      channels: Object.fromEntries(
        CHANNELS.map((c) => [
          c,
          c === 'digest' ? r.batch_mode === 'daily' : r.channels.includes(c),
        ]),
      ) as Record<NotificationChannel, boolean>,
      locked: LOCKED[r.kind] ?? [],
    })),
    quiet_hours_text:
      quiet?.from && quiet.to
        ? `Không gửi từ ${quiet.from} đến ${quiet.to}`
        : 'Không đặt giờ yên tĩnh',
    grouping_text: BATCH_TEXT[batch] ?? batch,
  };
}

export const getNotificationPreferences = async () =>
  readPrefs(await v1Get<PrefsWire>('/me/notification-prefs'));

/** PUT replaces the set; "Bản tin" on a row is its daily batch mode. */
export const saveNotificationPreferences = async (body: NotificationPreferences) =>
  readPrefs(
    await v1Send<PrefsWire>('PUT', '/me/notification-prefs', {
      prefs: body.events.map((e) => ({
        kind: e.key as Schemas['PrefIn']['kind'],
        channels: CHANNELS.filter((c) => c !== 'digest' && e.channels[c]) as PrefChannel[],
        batch_mode: e.channels.digest ? 'daily' : 'instant',
      })),
    } satisfies Schemas['PrefsPut']),
  );

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
