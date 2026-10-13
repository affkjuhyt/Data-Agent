import { listAlerts } from './alerts';
import { pathId, v1Get, v1Send, type Page } from './client';
import { getMe } from './me';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * Theo dõi (monitor service, P08, UC-08-01): a rule on a published metric that an agent checks on
 * a schedule. A2 has GET/POST /v1/monitors, PATCH /v1/monitors/{id} and :backtest. A monitor has
 * no name of its own: the list names it by its metric, and its state comes from its status and
 * its open alerts. The form's choices are the Data Space's published metrics (GET /v1/metrics)
 * and the dimensions of the chosen one (GET /v1/metrics/{id}). POST /v1/monitors creates the
 * monitor active and the monitor agent backtests it on the last 12 periods afterwards (BR-32).
 */

/** monitor.rule_kind (A3: threshold, change_vs_previous, seasonal_anomaly). */
export type RuleKind = 'threshold' | 'change_vs_previous' | 'seasonal_anomaly';
export type MonitorState = 'alerting' | 'early_warning' | 'normal' | 'insufficient_data' | 'muted';

/** A monitor as the list shows it in words. */
export interface Monitor {
  id: string;
  name: string;
  /** "NPL_RATIO v4 · tách theo region" */
  metric_text: string;
  rule_kind: RuleKind;
  /** "> 2,0% trong 1 ngày" */
  condition_text: string;
  state: MonitorState;
  last_fired_at: string | null;
  /** The newest alert, to open from the list. */
  last_alert_id: string | null;
  /** "Teams, email"; uncertified metrics only notify in the app. */
  channels_text: string;
  owner_name: string;
  owner_is_me: boolean;
  /** When the agent last evaluated it; MonitorView has no such field: null. */
  last_checked_at: string | null;
  /** BR-32: the backtest fired on more than 30% of its periods; null until it has run. */
  noise_warning: boolean | null;
}

/** GET /v1/monitors as the api sends it (MonitorView); the mock may add the screen's fields. */
export type MonitorWire = Schemas['MonitorView'] & Partial<Monitor>;

const CHANNEL_LABEL: Record<string, string> = {
  in_app: 'Trong ứng dụng',
  email: 'Email',
  teams: 'Teams',
  zalo: 'Zalo',
};

const WINDOW_LABEL: Record<string, string> = { '1d': '1 ngày', '3d': '3 ngày', '7d': '7 ngày' };
const BASE_LABEL: Record<string, string> = { week: 'tuần trước', month: 'tháng trước' };
const SENSITIVITY_LABEL: Record<string, string> = { low: 'thấp', medium: 'vừa', high: 'cao' };

const num = (v: unknown) => String(v ?? '').replace('.', ',');

/** The rule in words: the text the form saved with it, else said from its fields. */
export function conditionText(kind: string, raw: unknown): string {
  const rule = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  if (typeof rule.text === 'string' && rule.text) return rule.text;
  if (kind === 'threshold')
    return `${rule.direction === 'below' ? '<' : '>'} ${num(rule.value)}${
      rule.window ? ` trong ${WINDOW_LABEL[String(rule.window)] ?? rule.window}` : ''
    }`;
  if (kind === 'change_vs_previous')
    return `${rule.direction === 'up' ? 'tăng' : 'giảm'} > ${num(rule.value)}% so với ${
      BASE_LABEL[String(rule.base)] ?? 'kỳ trước'
    }`;
  if (kind === 'seasonal_anomaly')
    return `bất thường · độ nhạy ${SENSITIVITY_LABEL[String(rule.sensitivity)] ?? 'vừa'}`;
  return '—';
}

function channelsText(recipients: unknown): string {
  const channels = new Set<string>();
  for (const r of Array.isArray(recipients) ? recipients : []) {
    const list = (r as { channels?: unknown } | null)?.channels;
    if (Array.isArray(list)) list.forEach((c) => channels.add(String(c)));
  }
  return channels.size ? [...channels].map((c) => CHANNEL_LABEL[c] ?? c).join(', ') : '—';
}

type MetricNames = Map<string, { code: string; name: string; version: number | null }>;

function readMonitor(
  raw: MonitorWire,
  ctx: {
    metrics: MetricNames;
    me: string | null;
    latest: Map<string, { id: string; fired_at: string; open: boolean }>;
  },
): Monitor {
  const metric = ctx.metrics.get(raw.metric_id);
  const latest = ctx.latest.get(raw.id);
  const split = (raw.scope_filter as { split?: string } | null)?.split;
  return {
    id: raw.id,
    name: raw.name ?? metric?.name ?? '—',
    metric_text:
      raw.metric_text ??
      [
        metric ? `${metric.code}${metric.version ? ` v${metric.version}` : ''}` : null,
        split && split !== 'none' ? `tách theo ${split}` : null,
      ]
        .filter(Boolean)
        .join(' · '),
    rule_kind: raw.rule_kind as RuleKind,
    condition_text: raw.condition_text ?? conditionText(raw.rule_kind, raw.rule),
    state:
      raw.state ??
      (raw.status === 'paused' ? 'muted' : latest?.open ? 'alerting' : 'normal'),
    last_fired_at: raw.last_fired_at ?? latest?.fired_at ?? null,
    last_alert_id: raw.last_alert_id ?? latest?.id ?? null,
    channels_text: raw.channels_text ?? channelsText(raw.recipients),
    owner_name: raw.owner_name ?? (raw.owner_id === ctx.me ? 'Tôi' : 'Người khác'),
    owner_is_me: raw.owner_is_me ?? raw.owner_id === ctx.me,
    last_checked_at: raw.last_checked_at ?? null,
    noise_warning: raw.noise_warning ?? null,
  };
}

const metricNames = async (): Promise<MetricNames> => {
  const page = await v1Get<Page<Schemas['MetricView']>>('/metrics', { page_size: 500 });
  return new Map(
    page.items.map((m) => [
      m.id,
      { code: m.code, name: m.display_name ?? m.code, version: m.current_version },
    ]),
  );
};

const myId = async () => (await getMe().catch(() => null))?.id ?? null;

/**
 * GET /v1/monitors with the names of their metrics, the viewer, and each monitor's newest alert
 * (an open one makes it "Đang cảnh báo").
 */
export const listMonitors = async (): Promise<Page<Monitor>> => {
  const [page, metrics, me, alerts] = await Promise.all([
    v1Get<Page<MonitorWire>>('/monitors', { page_size: 200 }),
    metricNames().catch((): MetricNames => new Map()),
    myId(),
    listAlerts().catch(() => null),
  ]);
  const latest = new Map<string, { id: string; fired_at: string; open: boolean }>();
  for (const a of alerts?.items ?? []) {
    const seen = latest.get(a.monitor_id);
    const open = a.status !== 'closed' || Boolean(seen?.open);
    if (!seen || a.fired_at > seen.fired_at)
      latest.set(a.monitor_id, { id: a.id, fired_at: a.fired_at, open });
    else if (open) seen.open = true;
  }
  return { ...page, items: page.items.map((m) => readMonitor(m, { metrics, me, latest })) };
};

/** A2 pauses a monitor (PATCH status paused) and resumes it (active); there is no end date. */
export const muteMonitor = async (id: string, on: boolean) =>
  v1Send<MonitorWire>('PATCH', `/monitors/${pathId(id)}`, {
    status: on ? 'paused' : 'active',
  } satisfies Schemas['MonitorPatch']);

/** What "Tạo theo dõi" may choose from. */
export interface MonitorOptions {
  metrics: { id: string; label: string; name: string; certified: boolean }[];
  recipients: { value: string; label: string }[];
  channels: { value: string; label: string }[];
}

/** The dimensions and unit of the chosen metric, from its current version. */
export interface MetricShape {
  unit: string;
  splits: { value: string; label: string }[];
}

const CHANNELS = [
  { value: 'teams,email', label: 'Teams, email' },
  { value: 'teams', label: 'Teams' },
  { value: 'email', label: 'Email' },
  { value: 'zalo', label: 'Zalo' },
];

/** The published metrics of the Data Space (UC-08-01 precondition) and the viewer to notify. */
export const getMonitorOptions = async (): Promise<MonitorOptions> => {
  const [page, me] = await Promise.all([
    v1Get<Page<Schemas['MetricView']>>('/metrics', { status: 'active', page_size: 500 }),
    myId(),
  ]);
  return {
    metrics: page.items.map((m) => {
      const name = m.display_name ?? m.code;
      return {
        id: m.id,
        name,
        label: `${name}${m.display_name ? ` · ${m.code}` : ''} · ${m.certified ? 'đã chứng nhận' : 'chưa chứng nhận'}`,
        certified: m.certified,
      };
    }),
    recipients: me ? [{ value: me, label: 'Tôi' }] : [],
    channels: CHANNELS,
  };
};

export const getMetricShape = async (metricId: string): Promise<MetricShape> => {
  const detail = await v1Get<Schemas['MetricDetail']>(`/metrics/${pathId(metricId)}`);
  const current =
    detail.versions?.find((v) => v.version === detail.current_version) ?? detail.versions?.[0];
  return {
    unit: current?.unit ?? '',
    splits: [
      { value: 'none', label: 'Không tách' },
      ...(current?.allowed_dimensions ?? []).map((d) => ({ value: String(d), label: String(d) })),
    ],
  };
};

export interface MonitorDraft {
  metric_id: string;
  split: string;
  rule_kind: RuleKind;
  rule: Record<string, string | number>;
  schedule_cron: string;
  notify_mode: 'alert_only' | 'with_analysis';
  /** Principal ids, and the channels as "teams,email"; an uncertified metric gets in_app. */
  recipients: { who: string[]; channels: string };
}

/**
 * A2: POST /v1/monitors — the monitor agent backtests it afterwards (BR-32 above 30% is noisy).
 * Not checked against MonitorCreate: the generated type reads its free-form objects (rule,
 * scope_filter, recipients) as Record<string, never>.
 */
export const createMonitor = async (draft: MonitorDraft) =>
  v1Send<MonitorWire>('POST', '/monitors', {
    metric_id: draft.metric_id,
    scope_filter: draft.split === 'none' ? {} : { split: draft.split },
    rule_kind: draft.rule_kind,
    rule: draft.rule,
    schedule_cron: draft.schedule_cron,
    notify_mode: draft.notify_mode,
    recipients: draft.recipients.who.map((id) => ({
      principal_type: 'user',
      principal_id: id,
      channels: draft.recipients.channels.split(','),
    })),
  });
