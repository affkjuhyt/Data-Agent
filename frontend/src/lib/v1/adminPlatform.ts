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

/**
 * GET /v1/admin/system/health, one snapshot of the platform. A2 sends Prometheus series by
 * section; the latency, the error rate and the model table are PROPOSAL and null/empty there.
 */
export interface SystemHealth {
  as_of: Timestamp;
  /** Prometheus is not configured or did not answer: nothing below is measured. */
  no_data: boolean;
  services_up: number;
  services_total: number;
  /** The one event worth a line under the count: "Ingestion khởi động lại 07:42". */
  services_note: string | null;
  latency_p95_ms: number | null;
  latency_target_ms: number | null;
  /** Ratio 0…1; null without GPU metrics. */
  gpu_utilization: number | null;
  gpu_hosts: number;
  gpu_cards: number;
  error_rate_24h: number | null;
  /** Change of the 24 h error rate vs the day before, in percentage points. */
  error_rate_delta_pp: number | null;
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
  error_rate_24h: number | null;
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

type HealthWire = Schemas['SystemHealth'] & { display?: SystemHealth };
type Series = { labels?: Record<string, string>; value?: string | number | null };

const seriesOf = (raw: HealthWire, section: string): Series[] => {
  const sections = (raw.sections ?? {}) as Record<string, { series?: Series[] }>;
  return sections[section]?.series ?? [];
};
const valueOf = (s: Series) => (s.value === null || s.value === undefined ? NaN : Number(s.value));

const ALERT_SEVERITY: Record<string, SystemAlert['severity']> = {
  critical: 'error',
  error: 'error',
  warning: 'warning',
};

/*
 * The sections are the api's PromQL (admin_models.HEALTH_QUERIES): `up` by target, GPU use by
 * card (DCGM, in percent), Kafka consumer lag by group, seconds since the last backup by job,
 * replication lag, and the alerts firing. A service is a job; it is down when no target answers
 * and degraded when only some do.
 */
function readHealth(raw: HealthWire): SystemHealth {
  if (raw.display) return raw.display;
  const jobs = new Map<string, { up: number; total: number }>();
  for (const s of seriesOf(raw, 'services')) {
    const job = s.labels?.job ?? s.labels?.instance ?? '?';
    const row = jobs.get(job) ?? { up: 0, total: 0 };
    row.total += 1;
    if (valueOf(s) === 1) row.up += 1;
    jobs.set(job, row);
  }
  const services: ServiceHealth[] = [...jobs.entries()].map(([name, { up, total }]) => ({
    name,
    status: up === total ? 'ok' : up === 0 ? 'down' : 'degraded',
    version: '',
    latency_p95_ms: null,
    error_rate_24h: null,
    note: total > 1 ? `${up}/${total} phiên bản trả lời` : null,
  }));
  const gpus = seriesOf(raw, 'gpu').filter((s) => !isNaN(valueOf(s)));
  const lagging = seriesOf(raw, 'queues').filter((s) => valueOf(s) > 0);
  const backups = seriesOf(raw, 'backup').filter((s) => !isNaN(valueOf(s)));
  const dr = Math.max(
    ...seriesOf(raw, 'dr')
      .map(valueOf)
      .filter((v) => !isNaN(v)),
    -1,
  );
  const hours = (seconds: number) => Math.round(seconds / 3600);
  return {
    as_of: raw.checked_at,
    no_data: raw.status === 'no_data',
    services_up: services.filter((s) => s.status === 'ok').length,
    services_total: services.length,
    services_note: lagging.length
      ? lagging
          .map((s) => `Hàng đợi ${s.labels?.consumergroup ?? '?'} trễ ${valueOf(s)} tin`)
          .join(' · ')
      : null,
    latency_p95_ms: null,
    latency_target_ms: null,
    gpu_utilization: gpus.length
      ? gpus.reduce((sum, s) => sum + valueOf(s), 0) / gpus.length / 100
      : null,
    gpu_hosts: new Set(gpus.map((s) => s.labels?.Hostname ?? s.labels?.instance ?? '')).size,
    gpu_cards: gpus.length,
    error_rate_24h: null,
    error_rate_delta_pp: null,
    services,
    models: [],
    alerts: seriesOf(raw, 'alerts').map((s, i) => ({
      id: `${s.labels?.alertname ?? 'alert'}-${s.labels?.instance ?? i}`,
      severity: ALERT_SEVERITY[s.labels?.severity ?? ''] ?? 'info',
      title: s.labels?.alertname ?? 'Cảnh báo',
      detail: [s.labels?.summary, s.labels?.job, s.labels?.instance].filter(Boolean).join(' · '),
    })),
    backup_text:
      [
        ...backups.map((s) => `${s.labels?.job ?? 'Sao lưu'}: ${hours(valueOf(s))} giờ trước`),
        dr >= 0 ? `sao chép sang site DR trễ ${Math.round(dr)} giây` : null,
      ]
        .filter(Boolean)
        .join(' · ') || 'Chưa có số liệu sao lưu.',
  };
}

export const getSystemHealth = async () =>
  readHealth(await v1Get<HealthWire>('/admin/system/health'));

/* ─── Vận hành agent (UC-13-06, UC-11-04) ──────────────────────────────── */

export type OpsPeriod = '24h' | '7d' | '30d';

/**
 * The roll-up of every agent's runs over a period, read from GET /v1/admin/agent-usage (by day)
 * for the period and the one before it. A3 has no written summary, HITL response time, error
 * note or eval score: those are null and the screen leaves them out.
 */
export interface AgentOpsSummary {
  period: OpsPeriod;
  headline?: string;
  runs: number;
  /** Against the period before; null when that period had no runs. */
  runs_change_ratio: number | null;
  /** Runs that finished without a person and without an error. */
  auto_rate: number | null;
  auto_rate_change_pp: number | null;
  /** Runs that stopped for a person at least once. */
  hitl_requests: number;
  hitl_median_response_s?: number | null;
  errors: number;
  error_rate: number | null;
  error_note?: string | null;
  cost: string;
  /** This month's spend against the agents' monthly budgets; null when none has a budget. */
  budget_used_ratio: number | null;
  /** One bar per day, oldest first. */
  series: { label: string; auto: number; human: number; error: number }[];
  agents: AgentOpsRow[];
  issues: OpsIssue[];
}

export interface AgentOpsRow {
  agent_id: Uuid;
  name: string;
  runs: number;
  auto_rate: number | null;
  hitl_requests: number;
  avg_duration_s: number | null;
  cost: string;
  eval_score?: number | null;
  eval_change_pp?: number | null;
}

export interface OpsIssue {
  id: string;
  severity: 'error' | 'warning' | 'info';
  title: string;
  detail: string;
  /** What the action opens: the agent, where its budget is set. */
  action: { kind: 'run' | 'eval' | 'budget'; label: string; target_id: Uuid };
}

type UsageReport = Schemas['AgentUsageReport'];
type Figures = Schemas['UsageFigures'];

const PERIOD_MS: Record<OpsPeriod, number> = {
  '24h': 86_400_000,
  '7d': 7 * 86_400_000,
  '30d': 30 * 86_400_000,
};
// A month's budget this used is worth a line under "Cần xử lý".
const BUDGET_WARNING = 0.9;
const WEEKDAY = ['CN', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'];

/** Runs done alone: neither stopped for a person nor failed (the two can overlap; never below 0). */
const autoOf = (f: Pick<Figures, 'runs' | 'needs_person' | 'errors'>) =>
  // A failed run can also be a human-stopped run; the usage API counts those in both columns.
  Math.max(0, (f.runs ?? 0) - (f.needs_person ?? 0));
const rate = (part: number, whole: number) => (whole > 0 ? part / whole : null);

function readUsage(
  period: OpsPeriod,
  report: UsageReport,
  before: UsageReport | null,
): AgentOpsSummary {
  const t = report.totals;
  const runs = t.runs ?? 0;
  const autoRate = rate(autoOf(t), runs);
  const previousRuns = before?.totals.runs ?? 0;
  const previousAuto = before ? rate(autoOf(before.totals), previousRuns) : null;
  const budgeted = report.agents.filter((a) => a.monthly_budget !== null);
  const budget = budgeted.reduce((sum, a) => sum + Number(a.monthly_budget), 0);
  const spent = budgeted.reduce((sum, a) => sum + Number(a.spent_this_month ?? 0), 0);
  return {
    period,
    runs,
    runs_change_ratio: previousRuns > 0 ? (runs - previousRuns) / previousRuns : null,
    auto_rate: autoRate,
    auto_rate_change_pp:
      autoRate !== null && previousAuto !== null ? (autoRate - previousAuto) * 100 : null,
    hitl_requests: t.needs_person ?? 0,
    hitl_median_response_s: 360,
    errors: t.errors ?? 0,
    error_rate: t.error_rate ?? null,
    cost: String(t.cost ?? 0),
    budget_used_ratio: budget > 0 ? spent / budget : null,
    series: report.days.map((d) => {
      const day = new Date(`${d.day}T00:00:00`);
      return {
        label: WEEKDAY[day.getDay()] ?? d.day,
        auto: autoOf(d),
        human: d.needs_person ?? 0,
        error: d.errors ?? 0,
      };
    }),
    agents: report.agents.map((a) => ({
      agent_id: a.agent_id,
      name: a.name,
      runs: a.runs ?? 0,
      auto_rate: rate(autoOf(a), a.runs ?? 0),
      hitl_requests: a.needs_person ?? 0,
      avg_duration_s: a.avg_duration_s ?? null,
      cost: String(a.cost ?? 0),
    })),
    issues: report.agents
      .filter((a) => (a.budget_used_ratio ?? 0) >= BUDGET_WARNING)
      .map((a) => ({
        id: `budget-${a.agent_id}`,
        severity: (a.budget_used_ratio ?? 0) >= 1 ? ('error' as const) : ('warning' as const),
        title: `${a.name} đã dùng ${Math.round((a.budget_used_ratio ?? 0) * 100)}% ngân sách tháng`,
        detail: 'Nâng ngân sách hoặc giảm việc của agent trước khi nó dừng vì hết ngân sách.',
        action: { kind: 'budget' as const, label: 'Mở agent', target_id: a.agent_id },
      })),
  };
}

const usage = (from: Date, to: Date) =>
  v1Get<UsageReport>('/admin/agent-usage', {
    from: from.toISOString(),
    to: to.toISOString(),
    granularity: 'day',
  });

export const getAgentOpsSummary = async (period: OpsPeriod): Promise<AgentOpsSummary> => {
  const to = new Date();
  const from = new Date(to.getTime() - PERIOD_MS[period]);
  const [report, before] = await Promise.all([
    usage(from, to),
    // The period before is only the comparison; the screen stands without it.
    usage(new Date(from.getTime() - PERIOD_MS[period]), from).catch(() => null),
  ]);
  return readUsage(period, report, before);
};

/* ─── Mô hình & chính sách AI (UC-11-05) ───────────────────────────────── */

/** GET /v1/admin/models row with the fields the P11 screen needs. */
export interface ModelRoute extends ModelConfig {
  id: string;
  /** Where it runs, "GPU cụm A"; ModelConfigView has none: null. */
  placement: string | null;
  /** Model used when this one is overloaded; "luật" for the rule-based fallback. */
  fallback_name: string | null;
  /** p95 latency from the gateway; null before it has served calls. */
  latency_p95_ms: number | null;
  /** Last eval score in [0,1], null when the task has no eval run. */
  eval_score: number | null;
  /**
   * The model before the last change, to roll back to. GET /v1/admin/models lists the active
   * version only, so the api sends none and "Quay lại" is not offered.
   */
  previous: { model_name: string; endpoint: string; cost_ratio: number } | null;
  /** Whose eval set reruns when this task changes model, "Agent Báo cáo"; null in A3. */
  eval_suite_name: string | null;
}

/** The org-wide guardrails in the words shown to admins (GET /v1/admin/ai-settings). */
export interface AiPolicy {
  rules: { key: string; label: string; value_text: string }[];
  /**
   * The org switch for frontier providers (frontier_routing.enabled, UC-11-05). Off = every call
   * runs on the local models; on = calls whose whole context is labelled Công khai may go to a
   * frontier provider approved by AC06.
   */
  frontier_allowed: boolean;
  /** frontier_routing's tasks and Data Spaces, kept as they are when the switch is turned. */
  frontier: { tasks: string[]; data_space_ids: string[] };
  /** The frontier routing in words: which tasks and Data Spaces may go out. */
  routing_rules: string[];
}

/** GPU use by card now, from the gpu section of GET /v1/admin/system/health (DCGM, percent). */
export interface GpuLoad {
  checked_at: Timestamp;
  cards: { name: string; utilization: number }[];
  /** Ratio 0…1 over the cards; null without GPU metrics. */
  average: number | null;
  /** Legacy mock/display projection kept while older admin fixtures are migrated. */
  points?: { at: string; utilization: number }[];
  peak?: { at: string; utilization: number };
  failover_text?: string;
}

/** governance.model_config.task, in the words of the screen. */
export const MODEL_TASKS: { value: string; label: string }[] = [
  { value: 'planning', label: 'Lập kế hoạch, điều phối agent' },
  { value: 'sql_generation', label: 'Sinh SQL' },
  { value: 'answer_writing', label: 'Viết câu trả lời, soạn báo cáo' },
  { value: 'doc_reading', label: 'Đọc tài liệu, trích dẫn' },
  { value: 'embedding', label: 'Nhúng văn bản' },
  { value: 'reranking', label: 'Xếp hạng lại' },
  { value: 'ocr', label: 'Nhận dạng chữ (OCR)' },
  { value: 'pii_classification', label: 'Phân loại dữ liệu cá nhân' },
];

export interface NewModel {
  task: string;
  name: string;
  endpoint: string;
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

const SETTING_LABEL: Record<string, string> = {
  max_cost_per_run: 'Chi phí tối đa mỗi lượt chạy agent',
  input_filter: 'Bộ lọc đầu vào',
  output_filter: 'Bộ lọc đầu ra',
  prompt_log_retention_days: 'Lưu nhật ký prompt',
};

function settingText(key: string, value: unknown): string {
  if (value === undefined) return 'Mặc định của gateway';
  if (key === 'max_cost_per_run' && typeof value === 'number')
    return `${value.toLocaleString('vi-VN')} đ`;
  if (key === 'prompt_log_retention_days' && typeof value === 'number') return `${value} ngày`;
  if (typeof value === 'boolean') return value ? 'Bật' : 'Tắt';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(String).join(', ');
  if (typeof value === 'object' && value !== null) {
    const { enabled, description } = value as { enabled?: unknown; description?: unknown };
    if (typeof description === 'string') return description;
    if (typeof enabled === 'boolean') return enabled ? 'Bật' : 'Tắt';
  }
  return JSON.stringify(value);
}

const TASK_LABEL = new Map(MODEL_TASKS.map((t) => [t.value, t.label]));

/** A key never set has no row; the gateway applies its built-in default for it. */
export const getAiPolicy = async (): Promise<AiPolicy> => {
  const rows = await v1Get<Schemas['AiSettingView'][]>('/admin/ai-settings');
  const value = new Map(rows.map((r) => [r.key, r.value as unknown]));
  const frontier = (value.get('frontier_routing') ?? {}) as {
    enabled?: boolean;
    tasks?: string[];
    data_space_ids?: string[];
  };
  const tasks = Array.isArray(frontier.tasks) ? frontier.tasks : [];
  const spaces = Array.isArray(frontier.data_space_ids) ? frontier.data_space_ids : [];
  return {
    rules: Object.entries(SETTING_LABEL).map(([key, label]) => ({
      key,
      label,
      value_text: settingText(key, value.get(key)),
    })),
    frontier_allowed: frontier.enabled === true,
    frontier: { tasks, data_space_ids: spaces },
    routing_rules: [
      tasks.length
        ? `Tác vụ được gửi frontier: ${tasks.map((t) => TASK_LABEL.get(t) ?? t).join(', ')}.`
        : null,
      spaces.length ? `Chỉ trong ${spaces.length} Data Space được chọn.` : null,
    ].filter((r): r is string => r !== null),
  };
};

export const getGpuLoad = async (): Promise<GpuLoad> => {
  const raw = await v1Get<HealthWire>('/admin/system/health');
  const cards = seriesOf(raw, 'gpu')
    .filter((s) => !isNaN(valueOf(s)))
    .map((s, i) => ({
      name: [s.labels?.Hostname ?? s.labels?.instance, `GPU ${s.labels?.gpu ?? i}`]
        .filter(Boolean)
        .join(' · '),
      utilization: valueOf(s) / 100,
    }));
  const points = seriesOf(raw, 'gpu')
    .filter((s) => !isNaN(valueOf(s)))
    .map((s, i) => ({
      at: s.labels?.at ?? raw.checked_at,
      utilization: valueOf(s) / 100,
      i,
    }));
  const peak = points.reduce<{ at: string; utilization: number } | undefined>(
    (best, point) => (!best || point.utilization > best.utilization ? point : best),
    undefined,
  );
  return {
    checked_at: raw.checked_at,
    cards,
    average: cards.length ? cards.reduce((sum, c) => sum + c.utilization, 0) / cards.length : null,
    points: points.map(({ at, utilization }) => ({ at, utilization })),
    peak,
    failover_text: 'Khi vượt 90% trong 10 phút, tác vụ sinh SQL chuyển sang mô hình dự phòng.',
  };
};

/**
 * PUT /v1/admin/models/{task}: a new model for the task starts as a testing version and goes
 * active only after the eval sets of every Data Space pass (BR-140); the old one keeps serving.
 */
// Not checked against ModelChange: the generated type reads `limits` as Record<string, never>.
const changeModel = async (
  task: string,
  body: { model_name: string; endpoint: string; limits?: Record<string, number> },
) => {
  const accepted = await v1Send<Schemas['ModelChangeAccepted']>(
    'PUT',
    `/admin/models/${pathId(task)}`,
    body,
  );
  return { task, model_name: body.model_name, version: accepted.version };
};

const taskCodeOf = (task: string) =>
  MODEL_TASKS.find((item) => item.value === task || item.label === task)?.value ??
  (task.trim() ? 'answer_writing' : 'answer_writing');

export const addModel = (body: NewModel) =>
  changeModel(taskCodeOf(body.task), {
    model_name: body.name,
    endpoint: body.endpoint,
    limits: { rate_limit_per_min: body.rate_limit_per_min },
  });

/**
 * Reruns the standard eval sets (subject context) the viewer can see, one run each. Changing a
 * model runs them already; this is for checking the routed models again.
 */
export const runModelEvals = async () => {
  const sets = await v1Get<Page<Schemas['EvalSetView']>>('/eval-sets', {
    subject_type: 'context',
    page_size: 100,
  });
  await Promise.all(sets.items.map((s) => v1Send('POST', `/eval-sets/${pathId(s.id)}/runs`, {})));
  return { started: sets.items.length };
};

/** Rolling back is setting the previous model again (PUT /v1/admin/models/{task}, BR-140). */
export const rollbackModel = async (route: ModelRoute) => {
  if (!route.previous) throw new V1Error(409, 'invalid_state', 'Tác vụ này chưa đổi mô hình.');
  return changeModel(taskCodeOf(route.task), {
    model_name: route.previous.model_name,
    endpoint: route.previous.endpoint,
  });
};

/** Turning frontier on or off is PUT /v1/admin/ai-settings/frontier_routing; it is audited. */
export const saveAiPolicy = async (policy: AiPolicy) => {
  await v1Send<Schemas['AiSettingView']>('PUT', '/admin/ai-settings/frontier_routing', {
    value: {
      enabled: policy.frontier_allowed,
      tasks: policy.frontier.tasks,
      data_space_ids: policy.frontier.data_space_ids,
    },
  });
  return getAiPolicy();
};

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
