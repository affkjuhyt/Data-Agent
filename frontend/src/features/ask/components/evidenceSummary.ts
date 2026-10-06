/**
 * Pure helpers that turn an `EvidenceRun` into the chips a reader scans:
 * "approved metric v3", "1,204 rows", "3 tables", freshness, risks.
 * Kept free of React so both the inline strip and the side panel (and tests)
 * agree on what a run means.
 */
import { formatDateTime, formatNumber } from '../../../lib/formatVN';
import { FRESHNESS_LABELS } from '../labels';
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

  if (run.trust) {
    chips.push({
      key: 'trust',
      tone: run.trust === 'EXPLORATORY' ? 'warn' : 'ok',
      label: run.trust,
      title: run.trust === 'EXPLORATORY' ? 'Model-authored query' : 'Governed query source',
    });
  }

  if (run.trust === 'VERIFIED' || run.generated_by === 'verified_query') {
    chips.push({ key: 'metric', tone: 'ok', label: 'Verified query' });
  } else if (run.generated_by === 'semantic_layer' && run.metric?.metric) {
    const { key, version } = splitMetricRef(run.metric.metric);
    chips.push({
      key: 'metric',
      tone: 'ok',
      label: `Approved metric · ${key}${version ? ` v${version}` : ''}`,
      title: run.metric.owner ? `Owner: ${run.metric.owner}` : undefined,
    });
  } else {
    chips.push({ key: 'metric', tone: 'warn', label: 'Model-generated SQL', title: "Didn't match any approved metric — the SQL was written by the model, and passed through the read-only guard." });
  }

  if (run.row_count !== null && run.row_count !== undefined) {
    chips.push({ key: 'rows', tone: 'neutral', label: `${formatNumber(run.row_count)} rows` });
  }

  const tables = run.provenance?.tables?.length ?? 0;
  if (tables > 0) {
    chips.push({ key: 'tables', tone: 'neutral', label: `${tables} tables`, title: run.provenance!.tables!.map(t => t.table).join(', ') });
  }
  if (run.db_name) chips.push({ key: 'db', tone: 'neutral', label: run.db_name, title: run.db_type ? `Type: ${run.db_type}` : undefined });

  const status = run.provenance?.status;
  if (status) {
    const f = FRESHNESS_LABELS[status] ?? { label: `Freshness: ${status}`, tone: 'warn' as const };
    const asOf = run.provenance?.as_of ? ` · as of ${formatDateTime(run.provenance.as_of)}` : '';
    chips.push({ key: 'freshness', tone: f.tone, label: `${f.label}${asOf}` });
  }

  const findings = run.aggregation?.findings ?? [];
  if (findings.length) {
    chips.push({ key: 'aggregation', tone: 'bad', label: 'Aggregation risk', title: findings.map(f => f.detail).join('\n') });
  } else if (run.aggregation?.structurally_safe) {
    chips.push({ key: 'aggregation', tone: 'ok', label: 'No row duplication', title: 'The statement has no join that could duplicate rows before aggregating.' });
  }

  if (run.truncation?.reason) {
    chips.push({ key: 'truncation', tone: 'warn', label: 'Result truncated', title: `Reason: ${run.truncation.reason}` });
  }

  if (run.result_warnings?.length) {
    chips.push({ key: 'warnings', tone: 'warn', label: `${run.result_warnings.length} warning${run.result_warnings.length === 1 ? '' : 's'}`, title: run.result_warnings.map(w => w.detail || w.code).join('\n') });
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
