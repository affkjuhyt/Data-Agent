import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Dashboard có tóm tắt AI (UC-08-03): KPI tiles and charts pinned from result sets, a summary an
 * agent writes over them, a stale-data banner and the send schedule.
 *
 * PROPOSAL, all of it: /v1/dashboards is not in "Đặc tả API" yet (docs/v3/05-ui-v3.md §4,
 * "Khối chưa có API"). Paths follow A1: /v1/<plural>, `:verb` actions, cursor pages.
 */

type Uuid = string;
type Timestamp = string;

export interface DashboardKpi {
  id: Uuid;
  kind: 'kpi';
  label: string;
  value: string;
  /** `good` says whether the move is good for this metric, which sets the colour. */
  delta: { text: string; direction: 'up' | 'down' | 'flat'; good: boolean };
  /** The source behind it is late: the tile carries the clock mark. */
  stale: boolean;
}

export interface DashboardLine {
  id: Uuid;
  kind: 'line';
  title: string;
  points: { label: string; value: number }[];
  /** "1,84% · T9": the label on the last point. */
  last_label: string;
  stale: boolean;
}

export interface DashboardBars {
  id: Uuid;
  kind: 'bars';
  title: string;
  /** Signed values; `text` is the formatted number shown beside the bar. */
  bars: { label: string; value: number; text: string }[];
  stale: boolean;
}

export type DashboardTile = DashboardKpi | DashboardLine | DashboardBars;

/** A filter at the top ("Kỳ: tuần 41", "Vùng: tất cả"); its value goes in the GET query. */
export interface DashboardFilter {
  key: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
}

export type ScheduleKind = 'weekly' | 'daily' | 'on_new_data' | 'hourly_business' | 'cron';

/** The send schedule (dialog "Lịch chạy", shared with pipelines and source scans). */
export interface DashboardSchedule {
  kind: ScheduleKind;
  /** "07:00" for weekly / daily. */
  time: string | null;
  /** 1 = thứ Hai … 7 = Chủ Nhật, for weekly. */
  weekday: number | null;
  cron: string | null;
  skip_holidays: boolean;
  no_overlap: boolean;
  retry_on_error: boolean;
  /** "thứ Hai 07:00" — the schedule in words, for the header button. */
  text: string;
}

export interface Dashboard {
  id: Uuid;
  title: string;
  filters: DashboardFilter[];
  /** What the agent finds worth noting this period, one sentence per line. */
  summary: { lines: string[]; agent_name: string; written_at: Timestamp } | null;
  /** A source behind some tiles is late; the sentence says which and since when. */
  stale: { message: string; source_id: Uuid } | null;
  schedule: DashboardSchedule | null;
  tiles: DashboardTile[];
}

export interface DashboardSummary {
  id: Uuid;
  title: string;
  updated_at: Timestamp;
}

export const listDashboards = () =>
  v1Get<Page<DashboardSummary>>('/dashboards', { page_size: 100 });

export const getDashboard = async (id: string, filters: Record<string, string> = {}) =>
  v1Get<Dashboard>(`/dashboards/${pathId(id)}`, filters);

/** "Viết lại": the agent rewrites the summary over the current tiles and filters. */
export const summarizeDashboard = async (id: string, filters: Record<string, string> = {}) =>
  v1Send<NonNullable<Dashboard['summary']>>('POST', `/dashboards/${pathId(id)}:summarize`, {
    filters,
  });

export type ScheduleInput = Omit<DashboardSchedule, 'text'>;

/** "Dự kiến": the next runs in words for a schedule not saved yet. */
export const previewDashboardSchedule = async (id: string, schedule: ScheduleInput) =>
  v1Send<{ text: string }>('POST', `/dashboards/${pathId(id)}/schedule:preview`, schedule);

export const saveDashboardSchedule = async (id: string, schedule: ScheduleInput) =>
  v1Send<DashboardSchedule>('PUT', `/dashboards/${pathId(id)}/schedule`, schedule);
