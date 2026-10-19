import { formatDate, formatDateTime } from '../formatVN';
import { pathId, v1Get, v1Send, V1Error, type Page } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * Báo cáo định kỳ and Bản tin (monitor service, P08, UC-08-03, UC-08-04, UC-02-04, UC-02-07).
 * A2 serves the schedule list/detail (ReportScheduleView/Detail), PATCH of its title, schedule,
 * period rule, formats, recipients and status, :pause, :run-now and a run (ReportRunView). The
 * screen's words are said here from those rows. What A2 cannot store yet is PROPOSAL and comes
 * only from the mock: the sections and their order, switching the pre-send check on or off
 * (ReportPatch has neither, and the api ignores fields it does not know, so sending them would
 * answer 200 and change nothing), and a test sent to the caller alone (:run-now takes only
 * as_of and runs the report for every recipient).
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

/** A report schedule as "Thiết lập" draws it. */
export interface ReportSchedule {
  id: string;
  title: string;
  status: 'draft' | 'active' | 'paused';
  /** "Báo cáo định kỳ · gửi gần nhất 06/10/2026 07:00" */
  summary_text: string;
  /** null: the api keeps no section list, so the block says so instead of offering to edit. */
  sections: ReportSection[] | null;
  params: { label: string; value: string }[];
  delivery: { label: string; value: string }[];
  require_publish_check: boolean;
  /** Whether sections and the pre-send check can be saved from the screen (PROPOSAL PATCH). */
  settings_editable: boolean;
  /** "Đỗ Hải Yến duyệt trước 06:30. Quá hạn thì giữ lại, không tự gửi." */
  publish_check_text: string;
  /** Whether :run-now can send to the caller alone (PROPOSAL `only_me`); else it sends to all. */
  test_to_me: boolean;
  recipient_count: number;
  recent_runs: {
    id: string;
    at_text: string;
    status: ReportRunStatus;
    /** "có đính chính" for a sent run corrected later. */
    corrected: boolean;
    detail: string;
  }[];
}

/** PROPOSAL: what the mock sends beside the api's fields. */
type ReportDisplay = Partial<
  Pick<
    ReportSchedule,
    | 'summary_text'
    | 'params'
    | 'delivery'
    | 'publish_check_text'
    | 'test_to_me'
    | 'recent_runs'
  > & { sections: ReportSection[] }
>;

export type ReportScheduleWire = Schemas['ReportScheduleDetail'] & ReportDisplay;

const STATUSES = ['draft', 'active', 'paused'] as const;
const RUN_STATUSES: readonly ReportRunStatus[] = [
  'running',
  'sent',
  'partially_sent',
  'blocked_by_checks',
  'failed',
];

const KIND_LABEL: Record<string, string> = { report: 'Báo cáo định kỳ', digest: 'Bản tin' };
const TRIGGER_LABEL: Record<string, string> = { schedule: 'Theo lịch', run_now: 'Chạy ngay' };
const FORMAT_LABEL: Record<string, string> = { pdf: 'PDF', xlsx: 'Excel', html: 'Trong ứng dụng' };
const CHANNEL_LABEL: Record<string, string> = {
  in_app: 'Trong ứng dụng',
  email: 'Email',
  teams: 'Teams',
  zalo: 'Zalo',
};
const AS_OF_LABEL: Record<string, string> = {
  end_of_previous_day: 'Hết ngày hôm trước',
  end_of_previous_week: 'Hết tuần trước',
  end_of_previous_month: 'Hết tháng trước',
  end_of_previous_quarter: 'Hết quý trước',
};
const WEEKDAY = ['Chủ nhật', 'Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy'];

const two = (v: string) => v.padStart(2, '0');

/** The common cron shapes in words ("Thứ Hai hằng tuần, 07:30"); any other stays as written. */
export function cronText(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return cron;
  const [minute, hour, day, month, weekday] = parts;
  const at = `${two(hour)}:${two(minute)}`;
  if (month !== '*') return cron;
  if (day === '*' && weekday === '*') return `Hằng ngày, ${at}`;
  if (day === '*' && weekday === '1-5') return `Ngày làm việc, ${at}`;
  if (day === '*' && /^[0-7]$/.test(weekday)) return `${WEEKDAY[Number(weekday) % 7]} hằng tuần, ${at}`;
  if (/^\d+$/.test(day) && weekday === '*') return `Ngày ${day} hằng tháng, ${at}`;
  return cron;
}

function recipientsText(recipients: Schemas['RecipientView'][]): string {
  if (!recipients.length) return 'Chưa có người nhận';
  const people = recipients.filter((r) => r.principal_type === 'user').length;
  const groups = recipients.length - people;
  return [people && `${people} người`, groups && `${groups} nhóm`].filter(Boolean).join(', ');
}

function channelsText(recipients: Schemas['RecipientView'][]): string {
  const channels = new Set(recipients.flatMap((r) => r.channels));
  return channels.size ? [...channels].map((c) => CHANNEL_LABEL[c] ?? c).join(', ') : '—';
}

function readRun(run: Schemas['ReportRunView']): ReportSchedule['recent_runs'][number] {
  return {
    id: run.id,
    at_text: formatDateTime(run.finished_at ?? run.created_at),
    status: RUN_STATUSES.includes(run.status as ReportRunStatus)
      ? (run.status as ReportRunStatus)
      : 'running',
    corrected: false,
    detail: `${TRIGGER_LABEL[run.trigger] ?? run.trigger} · số liệu tới ${formatDate(run.as_of)}`,
  };
}

/** ReportScheduleDetail in the screen's words; a PROPOSAL field the mock sends wins. */
export function readReport(raw: ReportScheduleWire): ReportSchedule {
  const recipients = raw.recipients ?? [];
  const latest = raw.latest_run ? readRun(raw.latest_run) : null;
  return {
    id: raw.id,
    title: raw.title,
    status: STATUSES.includes(raw.status as ReportSchedule['status'])
      ? (raw.status as ReportSchedule['status'])
      : 'draft',
    summary_text:
      raw.summary_text ??
      `${KIND_LABEL[raw.kind] ?? 'Báo cáo'} · ${
        latest ? `lần chạy gần nhất ${latest.at_text}` : 'chưa chạy lần nào'
      }`,
    sections: raw.sections ?? null,
    params: raw.params ?? [
      { label: 'Kỳ số liệu', value: AS_OF_LABEL[raw.as_of_rule] ?? raw.as_of_rule },
      ...(raw.cutoff_time ? [{ label: 'Chốt số liệu', value: raw.cutoff_time.slice(0, 5) }] : []),
      {
        label: 'Định dạng',
        value: raw.formats.map((f) => FORMAT_LABEL[f] ?? f).join(', ') || '—',
      },
    ],
    delivery: raw.delivery ?? [
      { label: 'Lịch', value: cronText(raw.cron) },
      { label: 'Người nhận', value: recipientsText(recipients) },
      { label: 'Kênh', value: channelsText(recipients) },
    ],
    require_publish_check: raw.require_publish_check,
    settings_editable: raw.sections !== undefined,
    publish_check_text:
      raw.publish_check_text ??
      'Người duyệt kiểm tra bản soạn trước khi gửi; chưa duyệt thì giữ lại, không tự gửi.',
    test_to_me: raw.test_to_me ?? false,
    recipient_count: recipients.length,
    recent_runs: raw.recent_runs ?? (latest ? [latest] : []),
  };
}

export const listReports = async () =>
  v1Get<Page<Schemas['ReportScheduleView']>>('/reports', { page_size: 50, kind: 'report' });

export const getReport = async (id: string) =>
  readReport(await v1Get<ReportScheduleWire>(`/reports/${pathId(id)}`));

/** A2 PATCH takes `status`; `sections` and `require_publish_check` are PROPOSAL (see above). */
export const updateReport = async (
  id: string,
  patch: Partial<Pick<ReportSchedule, 'require_publish_check' | 'status'>> & {
    sections?: ReportSection[];
  },
) => v1Send<ReportScheduleWire>('PATCH', `/reports/${pathId(id)}`, patch);

export const pauseReport = async (id: string) =>
  v1Send<ReportScheduleWire>('POST', `/reports/${pathId(id)}:pause`);

/** A2 :run-now: a real run for every recipient, with today's figures. 202 names the run. */
export const runReportNow = async (id: string) =>
  v1Send<{ id: string }>('POST', `/reports/${pathId(id)}:run-now`, {});

/** PROPOSAL :run-now `only_me`: the test goes to the caller alone (the mock knows it). */
export const sendReportTest = async (id: string) =>
  v1Send<{ id: string }>('POST', `/reports/${pathId(id)}:run-now`, { only_me: true });

export interface DigestItem {
  /** The report run to read. */
  id: string;
  title: string;
  /** "06/10 · Agent Báo cáo", "29/09 · có 1 đính chính" */
  meta_text: string;
  unread: boolean;
}

/**
 * PROPOSAL GET /v1/me/digests; on the api the digests are the reader's notifications of kind
 * `digest`, whose `source_ref` is the report run they announce.
 */
export const listMyDigests = async (): Promise<Page<DigestItem>> => {
  try {
    return await v1Get<Page<DigestItem>>('/me/digests');
  } catch (error) {
    if (!(error instanceof V1Error) || error.status !== 404) throw error;
  }
  const page = await v1Get<Page<Schemas['NotificationView']>>('/me/notifications', {
    kind: 'digest',
    page_size: 50,
  });
  return {
    ...page,
    items: page.items
      .filter((item) => item.source_ref)
      .map((item) => ({
        id: item.source_ref as string,
        title: item.title,
        meta_text: item.body ?? formatDate(item.created_at),
        unread: item.read_at === null,
      })),
  };
};

/** A report run as "Bản tin · Đọc" draws it; the reading fields are PROPOSAL. */
export interface ReportRun {
  id: string;
  /** null: the reading pane takes the digest's title from the list. */
  title: string | null;
  meta_text: string;
  /** null until the report agent writes the reading (the api has only the rendered files). */
  changed: string | null;
  kpis: { label: string; value: string; delta: string; tone: 'up_bad' | 'up_good' | 'neutral' }[];
  why: string | null;
  decisions: { text: string; who_text: string; hitl_id: string | null }[];
  footer_text: string | null;
}

type ReportRunWire = Schemas['ReportRunView'] & Partial<ReportRun>;

const RUN_STATUS_TEXT: Record<string, string> = {
  running: 'đang soạn',
  sent: 'đã gửi',
  partially_sent: 'gửi một phần',
  blocked_by_checks: 'giữ lại vì chưa qua kiểm tra',
  failed: 'lỗi khi soạn',
};

export function readReportRun(raw: ReportRunWire): ReportRun {
  return {
    id: raw.id,
    title: raw.title ?? null,
    meta_text:
      raw.meta_text ??
      [
        formatDateTime(raw.finished_at ?? raw.created_at),
        RUN_STATUS_TEXT[raw.status] ?? raw.status,
        `số liệu tới ${formatDate(raw.as_of)}`,
      ].join(' · '),
    changed: raw.changed ?? null,
    kpis: raw.kpis ?? [],
    why: raw.why ?? null,
    decisions: raw.decisions ?? [],
    footer_text: raw.footer_text ?? null,
  };
}

export const getReportRun = async (id: string) =>
  readReportRun(await v1Get<ReportRunWire>(`/report-runs/${pathId(id)}`));
