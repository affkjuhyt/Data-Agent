import { V1Error, type Page, api, ok } from './client';
import type { components } from './schema';

/*
 * Cảnh báo (monitor service, P08, UC-08-02): what a monitor raised, the cause the Agent Phân tích
 * attached, and what people did with it. A2 has GET /v1/alerts and :ack, :assign, :close, :reopen;
 * an investigation is a new workspace (POST /v1/workspaces). The api's AlertView is read into the
 * screen's Alert below; the parts AlertView lacks (chart, analysis, activity) are null or empty.
 */

export type AlertStatus = 'open' | 'ack' | 'assigned' | 'closed';

/** monitor.alert with its monitor, series and analysis, as the screen draws it. */
export interface Alert {
  id: string;
  /** "#A-2291" */
  code: string;
  title: string;
  /** "Ba dòng: điều gì đổi, vì sao, cần làm gì" (monitor.alert.summary). */
  summary: string;
  status: AlertStatus;
  /** How a closed alert ended: dealt with, or a false alarm. */
  close_kind: 'resolved' | 'false_positive' | null;
  fired_at: string;
  monitor_id: string;
  monitor_name: string;
  /** "Teams, email cho 2 người" */
  delivered_text: string;
  occurrences: number;
  /** The 30 days around it, the threshold, and the day it crossed; AlertView has none: null. */
  chart: {
    metric_label: string;
    cutoff_text: string;
    series: { date: string; value: number }[];
    threshold: number;
    breach_from: string;
    unit: string;
  } | null;
  analysis: {
    summary: string;
    finished_text: string;
    factors: { label: string; contribution: number }[];
    record_count: number;
    sql: string;
  } | null;
  details: { label: string; value: string; mono?: boolean }[];
  activity: { at: string; text: string }[];
  end_rule_text: string;
  /** Who closed it and why, and what the agent learnt from a false alarm. */
  closed: { by: string; at: string; note: string; agent_note: string | null } | null;
}

/** GET /v1/alerts as the api sends it (AlertView); the mock adds the screen's detail. */
export type AlertWire = components['schemas']['AlertView'] & Partial<Omit<Alert, 'status'>>;

const number = (v: number | string | null) => (v === null ? '—' : String(v).replace('.', ','));

/** The first line of the three-line summary names the alert. */
function readAlert(raw: AlertWire): Alert {
  return {
    id: raw.id,
    code: raw.code ?? `#${raw.id.slice(0, 8)}`,
    title: raw.title ?? raw.summary.split('\n')[0],
    summary: raw.summary,
    status: raw.status as AlertStatus,
    close_kind: raw.close_kind ?? null,
    fired_at: raw.fired_at,
    monitor_id: raw.monitor_id,
    monitor_name: raw.monitor_name ?? '',
    delivered_text: raw.delivered_text ?? '',
    occurrences: raw.occurrences,
    chart: raw.chart ?? null,
    analysis: raw.analysis ?? null,
    details: raw.details ?? [
      { label: 'Giá trị', value: number(raw.value), mono: true },
      { label: 'Ngưỡng', value: number(raw.threshold_value), mono: true },
      ...(raw.period_label ? [{ label: 'Kỳ', value: raw.period_label }] : []),
    ],
    activity: raw.activity ?? [],
    end_rule_text: raw.end_rule_text ?? '',
    closed:
      raw.closed ??
      (raw.closed_at
        ? { by: '', at: raw.closed_at, note: raw.close_note ?? '', agent_note: null }
        : null),
  };
}

export const listAlerts = async (status?: AlertStatus) => {
  const page = await ok<Page<AlertWire>>(
    api.GET('/v1/alerts', { params: { query: { status, page_size: 500 } } }),
  );
  return { ...page, items: page.items.map(readAlert) };
};

/** A2 has no GET /v1/alerts/{id}: an alert is its row in the list. */
export const getAlert = async (id: string) => {
  const alert = (await listAlerts()).items.find((a) => a.id === id);
  if (!alert) throw new V1Error(404, 'not_found', 'Không tìm thấy cảnh báo.');
  return alert;
};

export const assignAlert = async (id: string, assigneeId: string) =>
  readAlert(
    await ok<AlertWire>(
      api.POST('/v1/alerts/{id}:assign', {
        params: { path: { id } },
        body: { assignee_id: assigneeId },
      }),
    ),
  );

/** `kind` is a PROPOSAL beside A2's close_note: a false alarm teaches the agent, BR-124 needs a note. */
export const closeAlert = async (id: string, note: string, kind: 'resolved' | 'false_positive') =>
  readAlert(
    await ok<AlertWire>(
      api.POST('/v1/alerts/{id}:close', {
        params: { path: { id } },
        body: { close_note: note, kind },
      }),
    ),
  );

/** A2 :reopen (added 12/10/2026): a closed alert opens again, with the reason why. */
export const reopenAlert = async (id: string, reason: string) =>
  readAlert(
    await ok<AlertWire>(
      api.POST('/v1/alerts/{id}:reopen', { params: { path: { id } }, body: { reason } }),
    ),
  );

/*
 * Opening an investigation is creating a workspace with the alert as its context (UC-08-02).
 * WorkspaceCreate.created_from has no "alert" yet, so the workspace starts blank and carries the
 * alert in its params.
 */
export const investigateAlert = async (alert: Pick<Alert, 'id' | 'code' | 'title' | 'summary'>) => {
  const ws = await ok<components['schemas']['WorkspaceView']>(
    api.POST('/v1/workspaces', {
      body: {
        title: `Điều tra ${alert.code} · ${alert.title}`.slice(0, 300),
        description: alert.summary,
        created_from: 'blank',
        params: { alert_id: alert.id },
      },
    }),
  );
  return { workspace_id: ws.id };
};
