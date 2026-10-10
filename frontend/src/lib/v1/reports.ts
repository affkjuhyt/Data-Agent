import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Báo cáo định kỳ and Bản tin (monitor service, P08, UC-08-03, UC-08-04, UC-02-04, UC-02-07).
 * A2 has POST /v1/reports, PATCH /v1/reports/{id}, :pause, :run-now and GET /v1/report-runs/{id};
 * reading one schedule, the list, its sections and recent sends, and the reader's own digests are
 * PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export interface ReportSection {
  key: string;
  title: string;
  /** "Agent viết, tối đa 3 ý" */
  hint: string;
  enabled: boolean;
  /** Personal data: sent inside the app only, never by email or Teams. */
  personal: boolean;
}

export type ReportRunStatus =
  | 'running'
  | 'sent'
  | 'partially_sent'
  | 'blocked_by_checks'
  | 'failed';

/** PROPOSAL: GET /v1/reports/{id} — monitor.report_schedule as "Thiết lập" reads it. */
export interface ReportSchedule {
  id: string;
  title: string;
  status: 'draft' | 'active' | 'paused';
  /** "Agent Báo cáo soạn từ phiên “Điều tra nợ xấu bán lẻ T9” · gửi gần nhất thứ Hai 06/10 07:00" */
  summary_text: string;
  sections: ReportSection[];
  params: { label: string; value: string }[];
  delivery: { label: string; value: string }[];
  require_publish_check: boolean;
  /** "Đỗ Hải Yến duyệt trước 06:30. Quá hạn thì giữ lại, không tự gửi." */
  publish_check_text: string;
  recent_runs: {
    id: string;
    at_text: string;
    status: ReportRunStatus;
    /** "có đính chính" for a sent run corrected later. */
    corrected: boolean;
    detail: string;
  }[];
}

export const listReports = async () =>
  v1Get<Page<Pick<ReportSchedule, 'id' | 'title' | 'status'>>>('/reports', { limit: 50 });

export const getReport = async (id: string) => v1Get<ReportSchedule>(`/reports/${pathId(id)}`);

/** A2 PATCH lists title, cron, … status, recipients; `sections` and `require_publish_check` are PROPOSAL. */
export const updateReport = async (
  id: string,
  patch: Partial<Pick<ReportSchedule, 'sections' | 'require_publish_check' | 'status'>>,
) => v1Send<ReportSchedule>('PATCH', `/reports/${pathId(id)}`, patch);

export const pauseReport = async (id: string) =>
  v1Send<ReportSchedule>('POST', `/reports/${pathId(id)}:pause`);

/** A2 :run-now; `only_me` (send the test to the caller alone) is PROPOSAL. */
export const sendReportTest = async (id: string) =>
  v1Send<{ id: string }>('POST', `/reports/${pathId(id)}:run-now`, { only_me: true });

/** PROPOSAL: GET /v1/me/digests — reports and digests sent to the reader, newest first. */
export interface DigestItem {
  id: string;
  title: string;
  /** "06/10 · Agent Báo cáo", "29/09 · có 1 đính chính" */
  meta_text: string;
  unread: boolean;
}

export const listMyDigests = async () => v1Get<Page<DigestItem>>('/me/digests', { limit: 50 });

/** A2: GET /v1/report-runs/{id} — ReportRun; the reading shape is PROPOSAL. */
export interface ReportRun {
  id: string;
  title: string;
  meta_text: string;
  changed: string;
  kpis: { label: string; value: string; delta: string; tone: 'up_bad' | 'up_good' | 'neutral' }[];
  why: string;
  decisions: { text: string; who_text: string; hitl_id: string | null }[];
  footer_text: string;
}

export const getReportRun = async (id: string) => v1Get<ReportRun>(`/report-runs/${pathId(id)}`);
