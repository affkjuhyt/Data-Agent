/**
 * Pure helpers that turn an `EvidenceRun` into the chips a reader scans:
 * "Chỉ số đã duyệt · v3", "1.204 dòng", "3 bảng", freshness, risks.
 * Kept free of React so both the inline strip and the side panel (and tests)
 * agree on what a run means.
 */
import { formatDateTime, formatNumber } from '../../../lib/formatVN';
import { FRESHNESS_LABELS } from '../labels';
import { runTrust, trustLabel } from '../trust';
import type { Evidence, EvidenceRun } from '../types';

export type Tone = 'ok' | 'warn' | 'bad' | 'neutral';

interface Chip {
  key: string;
  label: string;
  tone: Tone;
  title?: string;
}

/** "net_revenue@3" → { key: "net_revenue", version: "3" } */
export function splitMetricRef(ref: string | undefined | null): { key: string; version: string | null } {
  if (!ref) return { key: '', version: null };
  const at = ref.lastIndexOf('@');
  if (at === -1) return { key: ref, version: null };
  return { key: ref.slice(0, at), version: ref.slice(at + 1) };
}

export function runChips(run: EvidenceRun): Chip[] {
  const chips: Chip[] = [];

  // Every run shows its trust label, also when the backend sent none (invariant 14).
  const level = runTrust(run);
  chips.push({
    key: 'trust',
    tone: level === 'EXPLORATORY' ? 'warn' : 'ok',
    label: trustLabel(level),
    title: level === 'EXPLORATORY' ? 'Câu lệnh do mô hình viết' : 'Nguồn truy vấn có quản trị',
  });

  if (level === 'VERIFIED') {
    chips.push({ key: 'metric', tone: 'ok', label: 'Truy vấn đã kiểm' });
  } else if (run.generated_by === 'semantic_layer' && run.metric?.metric) {
    const { key, version } = splitMetricRef(run.metric.metric);
    chips.push({
      key: 'metric',
      tone: 'ok',
      label: `Chỉ số đã duyệt · ${key}${version ? ` v${version}` : ''}`,
      title: run.metric.owner ? `Người phụ trách: ${run.metric.owner}` : undefined,
    });
  } else {
    chips.push({ key: 'metric', tone: 'warn', label: 'SQL do mô hình viết', title: 'Không khớp chỉ số đã duyệt nào: câu lệnh do mô hình viết và đã qua lớp chặn chỉ-đọc.' });
  }

  if (run.row_count !== null && run.row_count !== undefined) {
    chips.push({ key: 'rows', tone: 'neutral', label: `${formatNumber(run.row_count)} dòng` });
  }

  const tables = run.provenance?.tables?.length ?? 0;
  if (tables > 0) {
    chips.push({ key: 'tables', tone: 'neutral', label: `${tables} bảng`, title: run.provenance!.tables!.map(t => t.table).join(', ') });
  }
  if (run.db_name) chips.push({ key: 'db', tone: 'neutral', label: run.db_name, title: run.db_type ? `Loại: ${run.db_type}` : undefined });

  const status = run.provenance?.status;
  if (status) {
    const f = FRESHNESS_LABELS[status] ?? { label: `Độ mới: ${status}`, tone: 'warn' as const };
    const asOf = run.provenance?.as_of ? ` · tới ${formatDateTime(run.provenance.as_of)}` : '';
    chips.push({ key: 'freshness', tone: f.tone, label: `${f.label}${asOf}` });
  }

  const findings = run.aggregation?.findings ?? [];
  if (findings.length) {
    chips.push({ key: 'aggregation', tone: 'bad', label: 'Rủi ro cộng gộp', title: findings.map(f => f.detail).join('\n') });
  } else if (run.aggregation?.structurally_safe) {
    chips.push({ key: 'aggregation', tone: 'ok', label: 'Không nhân dòng', title: 'Câu lệnh không có phép nối nào có thể nhân dòng trước khi cộng gộp.' });
  }

  if (run.truncation?.reason) {
    chips.push({ key: 'truncation', tone: 'warn', label: 'Kết quả bị cắt', title: `Lý do: ${run.truncation.reason}` });
  }

  if (run.result_warnings?.length) {
    chips.push({ key: 'warnings', tone: 'warn', label: `${run.result_warnings.length} cảnh báo`, title: run.result_warnings.map(w => w.detail || w.code).join('\n') });
  }

  return chips;
}

/** The first run that has rows to show — the table under the answer. */
export function primaryRun(evidence?: Evidence | null): EvidenceRun | null {
  if (!evidence?.runs?.length) return null;
  return evidence.runs.find(r => r.rows?.length) ?? evidence.runs[0];
}

export function hasEvidence(evidence?: Evidence | null): boolean {
  return !!evidence && (evidence.runs.length > 0 || evidence.warnings.length > 0);
}
