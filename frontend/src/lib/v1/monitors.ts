import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Theo dõi (monitor service, P08, UC-08-01): a rule on a certified metric that an agent checks on
 * a schedule. A2 has POST /v1/monitors, PATCH /v1/monitors/{id} and :backtest; the list, the form's
 * choices and the draft preview are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export type RuleKind = 'threshold' | 'change_vs_previous' | 'seasonal_anomaly' | 'forecast';
export type MonitorState = 'alerting' | 'early_warning' | 'normal' | 'insufficient_data' | 'muted';

/** PROPOSAL: GET /v1/monitors — monitor.monitor with what the list shows in words. */
export interface Monitor {
  id: string;
  name: string;
  /** "NPL_RATIO v4 · tách theo vùng" */
  metric_text: string;
  rule_kind: RuleKind;
  /** "> 2,0% trong 1 ngày" */
  condition_text: string;
  state: MonitorState;
  muted_until: string | null;
  last_fired_at: string | null;
  /** The newest alert, to open from the list. */
  last_alert_id: string | null;
  /** "Teams, email"; uncertified metrics only notify in the app. */
  channels_text: string;
  owner_name: string;
  owner_is_me: boolean;
  last_checked_at: string;
}

export const listMonitors = async () => v1Get<Page<Monitor>>('/monitors', { limit: 200 });

/** PROPOSAL: mute until a date — PATCH /v1/monitors/{id} {muted_until}; null unmutes. */
export const muteMonitor = async (id: string, mutedUntil: string | null) =>
  v1Send<Monitor>('PATCH', `/monitors/${pathId(id)}`, { muted_until: mutedUntil });

/** PROPOSAL: GET /v1/monitors/options — what "Tạo theo dõi" may choose from. */
export interface MonitorOptions {
  metrics: {
    id: string;
    label: string;
    certified: boolean;
    unit: string;
    /** Days of history; seasonal_anomaly needs 30 and forecast 60. */
    history_days: number;
  }[];
  scopes: { value: string; label: string }[];
  splits: { value: string; label: string; groups: string[] }[];
  recipients: { value: string; label: string }[];
  channels: { value: string; label: string }[];
  /** "Khoảng 2–4 phút và 9.000 đ mỗi lần." — the cost of the attached analysis. */
  analysis_cost_text: string;
}

export const getMonitorOptions = async () => v1Get<MonitorOptions>('/monitors/options');

export interface MonitorDraft {
  metric_id: string;
  scope_filter: { scope: string; split: string };
  rule_kind: RuleKind;
  rule: Record<string, string | number>;
  schedule_cron: string;
  notify_mode: 'alert_only' | 'with_analysis';
  recipients: { who: string; channels: string };
}

/**
 * PROPOSAL: POST /v1/monitors:preview — the draft against the last 90 days of one group: the
 * series, where it would have fired, and that in words. `series` is null when there is too little.
 */
export interface MonitorPreview {
  series: { date: string; value: number }[] | null;
  thresholds: { label: string; value: number; tone: 'error' | 'warning' }[];
  fires: number;
  periods: number;
  text: string;
  note: string | null;
}

export const previewMonitor = async (draft: MonitorDraft, group: string) =>
  v1Send<MonitorPreview>('POST', '/monitors:preview', { ...draft, group });

/** A2: POST /v1/monitors — returns the Monitor with its backtest (BR-32 above 30% is noisy). */
export const createMonitor = async (draft: MonitorDraft) =>
  v1Send<Monitor>('POST', '/monitors', draft);
