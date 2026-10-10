import { formatNumber, formatTime } from '../../lib/formatVN';
import type { IngestionJob, JobStatus, SourceRow } from '../../lib/v1/sources';

/*
 * How sources and jobs read in words on the P04 screens ("Nạp về · CDC", "10:41 · trễ 12 giây",
 * "Rủi ro tín dụng +2"). The status sentence itself comes from the API (status_label); the dot's
 * tone is derived here from the structured status so colour never disagrees with the state.
 */
type DotTone = 'success' | 'warning' | 'error' | 'info' | 'idle';

/** 12 → "12 giây", 540 → "9 phút", 21600 → "6 giờ". */
export function formatLag(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} giây`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút`;
  return `${Math.floor(seconds / 3600)} giờ`;
}

/** 1_280_000 → "1,28 tr", 412_000 → "412.000". */
export function formatRows(rows: number): string {
  return rows >= 1_000_000
    ? `${formatNumber(rows / 1_000_000, { maxFractionDigits: 2 })} tr`
    : formatNumber(rows);
}

export function sourceTone(source: SourceRow): DotTone {
  if (source.status === 'needs_fix') return 'error';
  if (source.schema_drift) return 'warning';
  if (source.status === 'testing') return 'info';
  if (source.status === 'active') return 'success';
  return 'idle';
}

export function modeLabel(source: Pick<SourceRow, 'mode' | 'refresh_text'>): string {
  if (source.mode === 'federated') return 'Truy vấn tại chỗ';
  return source.refresh_text ? `Nạp về · ${source.refresh_text}` : 'Nạp về';
}

/** "Dữ liệu mới nhất": a federated source with no copy reads "theo nguồn". */
export function freshnessText(source: SourceRow): string {
  if (!source.last_refreshed_at) return source.mode === 'federated' ? 'theo nguồn' : '—';
  const parts = [formatTime(source.last_refreshed_at)];
  if (source.lag_seconds) parts.push(`trễ ${formatLag(source.lag_seconds)}`);
  else if (source.late_by_minutes) parts.push(`trễ ${formatLag(source.late_by_minutes * 60)}`);
  if (source.held_records) parts.push(`giữ ${formatNumber(source.held_records)} bản ghi`);
  return parts.join(' · ');
}

/** One or two spaces by name, then "+N"; four or more as a count. */
export function dataSpacesText(source: SourceRow): string {
  const spaces = source.data_spaces;
  if (spaces.length === 0) return '—';
  if (spaces.length >= 4) return `${spaces.length} Data Space`;
  return spaces.length === 1 ? spaces[0].name : `${spaces[0].name} +${spaces.length - 1}`;
}

/** "12 nguồn · 11 đúng lịch · 1 cần sửa". Drafts are not counted: they are not connected yet. */
export function sourcesSummary(sources: SourceRow[]): string {
  const live = sources.filter((s) => s.status !== 'draft' && s.status !== 'archived');
  const broken = live.filter((s) => s.status === 'needs_fix').length;
  const parts = [`${live.length} nguồn`, `${live.length - broken} đúng lịch`];
  if (broken) parts.push(`${broken} cần sửa`);
  return parts.join(' · ');
}

const METHOD: Record<IngestionJob['method'], string> = {
  cdc: 'CDC',
  schedule: 'Theo lịch',
  incremental: 'Tăng dần',
  continuous: 'Liên tục',
  new_files: 'Tệp mới',
};

export const methodLabel = (job: IngestionJob) => METHOD[job.method];

const JOB_STATUS: Record<JobStatus, { label: string; tone: DotTone }> = {
  running: { label: 'Đang chạy', tone: 'success' },
  on_time: { label: 'Đúng hạn', tone: 'success' },
  late: { label: 'Trễ', tone: 'warning' },
  error: { label: 'Lỗi', tone: 'error' },
  paused: { label: 'Tạm dừng', tone: 'warning' },
};

export function jobStatus(job: IngestionJob): { label: string; tone: DotTone } {
  const s = JOB_STATUS[job.status];
  return job.status_reason ? { ...s, label: `${s.label} · ${job.status_reason}` } : s;
}

export type JobFilter = 'all' | 'problem' | 'running' | 'paused';

export function matchesJobFilter(job: IngestionJob, filter: JobFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'problem') return job.status === 'error' || job.status === 'paused' || job.late;
  if (filter === 'running') return job.status === 'running';
  return job.status === 'paused';
}

/** "27 job · 24 đúng hạn · 1 trễ · 1 lỗi · 1 tạm dừng do cấu trúc nguồn thay đổi" */
export function jobsSummary(jobs: IngestionJob[]): string {
  const count = (pred: (j: IngestionJob) => boolean) => jobs.filter(pred).length;
  const paused = count((j) => j.status === 'paused');
  const errors = count((j) => j.status === 'error');
  const late = count((j) => j.late && j.status !== 'error' && j.status !== 'paused');
  const parts = [`${jobs.length} job`, `${jobs.length - paused - errors - late} đúng hạn`];
  if (late) parts.push(`${late} trễ`);
  if (errors) parts.push(`${errors} lỗi`);
  if (paused) parts.push(`${paused} tạm dừng`);
  return parts.join(' · ');
}
