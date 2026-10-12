import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * Cảnh báo (monitor service, P08, UC-08-02): what a monitor raised, the cause the Agent Phân tích
 * attached, and what people did with it. A2 has GET /v1/alerts and :ack, :assign, :close; one alert
 * by id, reopening, opening an investigation and flagging a wrong analysis are PROPOSALs. The
 * api's AlertView is read into the screen's Alert below; its PROPOSAL parts are null or derived.
 */

export type AlertStatus = 'open' | 'ack' | 'assigned' | 'closed';

/** PROPOSAL: GET /v1/alerts/{id} — monitor.alert with its monitor, series and analysis. */
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
  /** PROPOSAL: the 30 days around it, the threshold, and the day it crossed; null without it. */
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

/** A2: GET /v1/alerts — open ones first, for the rail without an id. */
/** GET /v1/alerts as the api sends it (AlertView), with the PROPOSAL detail when present. */
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
  const page = await v1Get<Page<AlertWire>>('/alerts', { status, page_size: 500 });
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
    await v1Send<AlertWire>('POST', `/alerts/${pathId(id)}:assign`, { assignee_id: assigneeId }),
  );

/** `kind` is a PROPOSAL beside A2's close_note: a false alarm teaches the agent, BR-124 needs a note. */
export const closeAlert = async (id: string, note: string, kind: 'resolved' | 'false_positive') =>
  readAlert(
    await v1Send<AlertWire>('POST', `/alerts/${pathId(id)}:close`, { close_note: note, kind }),
  );

/** PROPOSAL */
export const reopenAlert = async (id: string) =>
  readAlert(await v1Send<AlertWire>('POST', `/alerts/${pathId(id)}:reopen`));

/** PROPOSAL: a notebook seeded with the alert and its analysis. */
export const investigateAlert = async (id: string) =>
  v1Send<{ workspace_id: string }>('POST', `/alerts/${pathId(id)}:investigate`);

/** PROPOSAL: the attached analysis is wrong; goes to the agent's evaluation set. */
export const disputeAlertAnalysis = async (id: string, reason: string) =>
  v1Send<{ id: string }>('POST', `/alerts/${pathId(id)}:dispute-analysis`, { reason });
