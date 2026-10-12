import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Cảnh báo (monitor service, P08, UC-08-02): what a monitor raised, the cause the Agent Phân tích
 * attached, and what people did with it. A2 has GET /v1/alerts and :ack, :assign, :close; one alert
 * by id, reopening, opening an investigation and flagging a wrong analysis are PROPOSALs.
 */

export type AlertStatus = 'open' | 'ack' | 'assigned' | 'closed';

/** PROPOSAL: GET /v1/alerts/{id} — monitor.alert with its monitor, series and analysis. */
export interface Alert {
  id: string;
  /** "#A-2291" */
  code: string;
  title: string;
  status: AlertStatus;
  /** How a closed alert ended: dealt with, or a false alarm. */
  close_kind: 'resolved' | 'false_positive' | null;
  fired_at: string;
  monitor_id: string;
  monitor_name: string;
  /** "Teams, email cho 2 người" */
  delivered_text: string;
  occurrences: number;
  /** The 30 days around it, the threshold, and the day it crossed. */
  chart: {
    metric_label: string;
    cutoff_text: string;
    series: { date: string; value: number }[];
    threshold: number;
    breach_from: string;
    unit: string;
  };
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
export const listAlerts = async (status?: AlertStatus) =>
  v1Get<Page<Alert>>('/alerts', { status, limit: 50 });

export const getAlert = async (id: string) => v1Get<Alert>(`/alerts/${pathId(id)}`);

export const assignAlert = async (id: string, assigneeId: string) =>
  v1Send<Alert>('POST', `/alerts/${pathId(id)}:assign`, { assignee_id: assigneeId });

/** `kind` is a PROPOSAL beside A2's close_note: a false alarm teaches the agent, BR-124 needs a note. */
export const closeAlert = async (id: string, note: string, kind: 'resolved' | 'false_positive') =>
  v1Send<Alert>('POST', `/alerts/${pathId(id)}:close`, { close_note: note, kind });

/** PROPOSAL */
export const reopenAlert = async (id: string) =>
  v1Send<Alert>('POST', `/alerts/${pathId(id)}:reopen`);

/** PROPOSAL: a notebook seeded with the alert and its analysis. */
export const investigateAlert = async (id: string) =>
  v1Send<{ workspace_id: string }>('POST', `/alerts/${pathId(id)}:investigate`);

/** PROPOSAL: the attached analysis is wrong; goes to the agent's evaluation set. */
export const disputeAlertAnalysis = async (id: string, reason: string) =>
  v1Send<{ id: string }>('POST', `/alerts/${pathId(id)}:dispute-analysis`, { reason });
