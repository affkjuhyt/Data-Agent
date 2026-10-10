import type { ExploreFilter, ExploreDimension, ExploreResult } from '../../lib/v1/explore';

const MINUS = '−';

/** "1,71%", "3.412", the Vietnamese separators the frames use. */
export function formatValue(value: number | null, unit: ExploreResult['unit']): string {
  if (value === null) return '—';
  if (unit === 'percent')
    return `${value.toLocaleString('vi-VN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  return value.toLocaleString('vi-VN', { maximumFractionDigits: 1 });
}

/** "+0,61", "−0,04": a signed change with the typographic minus. */
export function formatDelta(delta: number | null, unit: ExploreResult['unit']): string {
  if (delta === null) return '—';
  const digits = unit === 'percent' ? 2 : 0;
  const text = Math.abs(delta).toLocaleString('vi-VN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${delta > 0 ? '+' : delta < 0 ? MINUS : ''}${text}`;
}

/**
 * The value part of a filter pill: "= Bán lẻ", "∈ Q2, Q3/2026" — a year shared by every value
 * is written once, after the last one, as in the frame.
 */
export function filterText(filter: ExploreFilter, dimension: ExploreDimension | undefined): string {
  const labels = filter.values.map(
    (value) => dimension?.values.find((v) => v.value === value)?.label ?? value,
  );
  const years = labels.map((l) => /\/(\d{4})$/.exec(l)?.[1]);
  const shared = years.length > 1 && years.every((y) => y && y === years[0]);
  const shown = shared
    ? labels.map((l, i) => (i === labels.length - 1 ? l : l.replace(/\/\d{4}$/, '')))
    : labels;
  return `${filter.op === 'eq' && shown.length === 1 ? '=' : '∈'} ${shown.join(', ')}`;
}
