import { formatDate, formatNumber, formatTime } from '../../lib/formatVN';
import type { VaultSession } from '../../lib/vault/session';
import type { MemoryItem, Thesis } from '../../lib/v1/exec';

/*
 * What the vault's ciphertexts hold once opened on the device. The server never sees these shapes;
 * they are the contract between this screen and the workflows that seal evidence and scores to
 * the vault's box key (tab 5 G10.3–G10.4).
 */
export type Comparator = 'gte' | 'lte' | 'gt' | 'lt';

export interface ThesisCondition {
  ref_kind: 'metric' | 'data_product';
  metric_ref: string;
  metric_label: string;
  /** "giá trần E5RON92": the slice of the metric the thesis is about. */
  scope_label?: string;
  comparator: Comparator;
  threshold: number;
  unit: string;
  period_label: string;
}

export interface ThesisContent {
  statement: string;
  rationale: string;
  confidence: number;
  tags: string[];
  condition: ThesisCondition;
}

export interface MemoryContent {
  text: string;
  tag: string;
}

export interface SealedEvidence {
  key: string;
  source_kind: string;
  title: string;
  text: string;
  query_run: string | null;
  source: string;
}

export interface SealedScore {
  observed: string;
  receipt: string;
  condition_matches: boolean;
}

export const COMPARATOR: Record<Comparator, string> = { gte: '≥', lte: '≤', gt: '>', lt: '<' };

export const thresholdText = (c: ThesisCondition) =>
  `${COMPARATOR[c.comparator]} ${formatNumber(c.threshold)}${c.unit ? ` ${c.unit}` : ''}`;

/** "Giá trần E5RON92 kỳ điều hành 22/10/2026 ≥ 20.000 đ/lít" */
export function conditionTitle(c: ThesisCondition) {
  const what = c.scope_label ?? c.metric_label;
  return `${what.charAt(0).toUpperCase()}${what.slice(1)}${c.period_label ? ` kỳ ${c.period_label}` : ''} ${thresholdText(c)}`;
}

export const shortDateTime = (iso: string) => `${formatDate(iso).slice(0, 5)} ${formatTime(iso)}`;

/** A thesis with its content opened; null when this vault's key does not open it. */
export interface OpenThesis extends Thesis {
  content: ThesisContent | null;
}

export interface OpenMemory extends MemoryItem {
  content: MemoryContent | null;
}

function tryOpen<T>(
  session: VaultSession,
  ct: string | null,
  dek: string | null,
  id: string,
  version: number,
) {
  if (!ct || !dek) return null;
  try {
    return session.decrypt<T>(ct, dek, id, version);
  } catch {
    return null;
  }
}

export const openTheses = (session: VaultSession, items: Thesis[]): OpenThesis[] =>
  items.map((t) => ({
    ...t,
    content: tryOpen<ThesisContent>(session, t.content_ct, t.wrapped_dek, t.id, t.content_version),
  }));

export const openMemory = (session: VaultSession, items: MemoryItem[]): OpenMemory[] =>
  items.map((m) => ({
    ...m,
    content: tryOpen<MemoryContent>(session, m.content_ct, m.wrapped_dek, m.id, m.content_version),
  }));

export function openSealedSafe<T>(session: VaultSession, sealed: string): T | null {
  try {
    return session.openSealed<T>(sealed);
  } catch {
    return null;
  }
}

/*
 * The local model's first proposal of a scoring condition from the free-text claim (UC-16-01 step
 * 2): the number and the direction the leader wrote. It only pre-fills the fields; the leader picks
 * the certified metric and confirms every part before anything is sealed.
 */
export function proposeCondition(statement: string): Partial<ThesisCondition> {
  // The number that follows the direction word, so a date earlier in the claim is not taken.
  const match =
    /(dưới|thấp hơn|không quá|tối đa|≤|<=|<|trên|cao hơn|ít nhất|tối thiểu|vượt|≥|>=|>)\s*(\d+(?:[.,]\d+)?)\s*(nghìn|ngàn|k|%|tỷ)?/.exec(
      statement.toLowerCase(),
    );
  if (!match) return {};
  const below = /^(dưới|thấp hơn|không quá|tối đa|≤|<=|<)$/.test(match[1]);
  const raw = match[2];
  // "20.000" is a thousands separator in Vietnamese, "5,0" a decimal comma.
  let value = Number(raw.replace(/\.(?=\d{3}\b)/g, '').replace(',', '.'));
  if (match[3] === 'nghìn' || match[3] === 'ngàn' || match[3] === 'k') value *= 1000;
  return {
    comparator: below ? 'lte' : 'gte',
    threshold: value,
    ...(match[3] === '%' ? { unit: '%' } : {}),
  };
}
