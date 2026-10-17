/**
 * "Hệ thống hiểu là" (P01 · Hỏi đáp, 2296:18531): how the system read the question, as
 * label/value chips — Chỉ số, Phạm vi, Chiều, Dữ liệu. Built only from what the result carries
 * (the approved metric's contract and the data's as-of); a model-written query has no contract,
 * so it gets no chips rather than guessed ones.
 */
import { formatDate } from '../../lib/formatVN';
import { splitMetricRef } from './components/evidenceSummary';
import type { EvidenceRun } from './types';

export interface ReadingChip {
  key: string;
  label: string;
  value: string;
  /** carried over from the question before (UC-01-04: "từ câu trước") */
  inherited?: boolean;
}

export function interpretationChips(run: EvidenceRun | null | undefined): ReadingChip[] {
  if (!run) return [];
  const chips: ReadingChip[] = [];
  const metric = run.generated_by === 'semantic_layer' ? run.metric : null;
  if (metric?.metric) {
    const { key, version } = splitMetricRef(metric.metric);
    chips.push({ key: 'metric', label: 'Chỉ số', value: `${key}${version ? ` v${version}` : ''}` });
    const filters = Array.isArray(metric.filters)
      ? metric.filters.map(f => (typeof f === 'string' ? f : JSON.stringify(f)))
      : [];
    if (filters.length) chips.push({ key: 'scope', label: 'Phạm vi', value: filters.join(', ') });
    if (metric.dimensions?.length) chips.push({ key: 'dimension', label: 'Chiều', value: metric.dimensions.join(', ') });
  }
  const asOf = run.provenance?.as_of;
  if (asOf && chips.length) chips.push({ key: 'data', label: 'Dữ liệu', value: `tới ${formatDate(asOf)}` });
  return chips;
}
