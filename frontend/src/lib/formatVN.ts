/**
 * Vietnamese number / currency / date formatting — one place, no locale guessing.
 *
 * Everything user-facing in the data agent goes through here instead of
 * `toLocaleString()` without arguments (which formats according to the
 * viewer's browser, so the same number reads "1,284,560" on one laptop and
 * "1.284.560" on the next). The rules follow Vietnamese convention
 * (docs/ankt/UI-DESIGN.md section 3):
 *
 *   - thousands grouped with "."  → 1.284.560.000
 *   - decimals with ","           → 1,28
 *   - currency suffixed with "₫"  → 1.284.560.000 ₫
 *   - multipliers as words        → 1,28 tỷ · 12,5 triệu · 3,2 nghìn
 *   - dates as dd/MM/yyyy, weeks start on Monday
 *
 * This file used to emit US convention despite its name. That is a worse bug
 * than it looks: a debt bulletin writing "128,4" means 128.4, so rendering the
 * same figure as "128.4" — or worse, reading it back as 128,400 — produces an
 * answer that is wrong while looking entirely credible.
 *
 * The convention applies to OUTPUT only. `toNumber` still parses "." as the
 * decimal point because its inputs are machine values (Postgres NUMERIC arrives
 * as "1284560000.00", JSON numbers are always US-format); "fixing" the parser
 * to match the display convention would corrupt every value on the way in.
 *
 * Implemented by hand rather than through Intl so the output is identical in
 * every runtime (Node without full ICU, older WebViews) and fully unit-testable.
 */

const GROUP = '.';
const DECIMAL = ',';
const DASH = '—';

/** getDay() index for the first day of the week (Monday === 1). */
export const WEEK_STARTS_ON = 1;

type Numeric = number | string | bigint | null | undefined;

interface NumberOptions {
  /** Maximum fraction digits (default: 0 for integers, 2 otherwise). */
  maxFractionDigits?: number;
  /** Always show at least this many fraction digits. */
  minFractionDigits?: number;
}

/**
 * Coerce to a finite number, or null. Accepts numeric strings ("1200.50").
 *
 * Machine format, deliberately: the "." here is a decimal point, not a
 * thousands group. Callers pass API/database values, never text a user typed.
 */
export function toNumber(value: Numeric): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const s = value.trim();
  if (!/^[-+]?(\d+(\.\d+)?|\.\d+)([eE][-+]?\d+)?$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function groupInteger(digits: string): string {
  let out = '';
  for (let i = 0; i < digits.length; i++) {
    const fromEnd = digits.length - i;
    out += digits[i];
    if (fromEnd > 1 && (fromEnd - 1) % 3 === 0) out += GROUP;
  }
  return out;
}

/** 1284560000 → "1.284.560.000"; 1.2845 → "1,28"; "1200.50" → "1.200,5". */
export function formatNumber(value: Numeric, opts: NumberOptions = {}): string {
  const n = toNumber(value);
  if (n === null) return DASH;

  const isInt = Number.isInteger(n);
  const max = opts.maxFractionDigits ?? (isInt ? 0 : 2);
  const min = Math.min(opts.minFractionDigits ?? 0, max);

  // toFixed handles rounding; avoid exponent notation for very large values.
  let fixed = Math.abs(n).toFixed(max);
  if (fixed.includes('e')) fixed = Math.abs(n).toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: max });

  // Split on "." — toFixed and en-US toLocaleString both emit machine format,
  // so this is still the decimal point even though "." groups on the way out.
  let [intPart, fracPart = ''] = fixed.split('.');
  // strip trailing zeros beyond the minimum
  while (fracPart.length > min && fracPart.endsWith('0')) fracPart = fracPart.slice(0, -1);

  const sign = n < 0 && (Number(intPart) !== 0 || Number(fracPart) !== 0) ? '-' : '';
  const grouped = groupInteger(intPart);
  return fracPart ? `${sign}${grouped}${DECIMAL}${fracPart}` : `${sign}${grouped}`;
}

// Words, not B/M/K: UI-DESIGN section 3 asks for multipliers spelled out, and
// "nghìn tỷ" (10^12) earns its place at the top because Vietnamese fiscal
// reporting states public debt and budget figures in it — 3.109,2 nghìn tỷ
// đồng is how the source itself writes the number.
const COMPACT_UNITS: Array<[number, string]> = [
  [1e12, 'nghìn tỷ'],
  [1e9, 'tỷ'],
  [1e6, 'triệu'],
  [1e3, 'nghìn'],
];

/** 1284560000 → "1,28 tỷ"; 12500000 → "12,5 triệu"; 950 → "950". */
export function formatCompact(value: Numeric, maxFractionDigits = 2): string {
  const n = toNumber(value);
  if (n === null) return DASH;
  const abs = Math.abs(n);
  for (const [threshold, unit] of COMPACT_UNITS) {
    if (abs >= threshold) {
      return `${formatNumber(n / threshold, { maxFractionDigits })} ${unit}`;
    }
  }
  return formatNumber(n);
}

/** "1.284.560.000 ₫" — or, with `compact`, "1,28 tỷ ₫" for tight cells. */
export function formatCurrency(value: Numeric, opts: { compact?: boolean; currency?: string } = {}): string {
  const n = toNumber(value);
  if (n === null) return DASH;
  const symbol = opts.currency ?? '₫';
  const body = opts.compact ? formatCompact(n) : formatNumber(n, { maxFractionDigits: 0 });
  return `${body} ${symbol}`;
}

/** 0.125 → "12,5%". Takes a ratio, not a percentage. */
export function formatPercent(ratio: Numeric, maxFractionDigits = 1): string {
  const n = toNumber(ratio);
  if (n === null) return DASH;
  return `${formatNumber(n * 100, { maxFractionDigits })}%`;
}

type DateInput = Date | string | number | null | undefined;

export function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === '') return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** dd/MM/yyyy */
export function formatDate(value: DateInput): string {
  const d = toDate(value);
  if (!d) return DASH;
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}

/** HH:mm (24h) */
export function formatTime(value: DateInput, withSeconds = false): string {
  const d = toDate(value);
  if (!d) return DASH;
  const base = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
}

/** dd/MM/yyyy HH:mm */
export function formatDateTime(value: DateInput): string {
  const d = toDate(value);
  if (!d) return DASH;
  return `${formatDate(d)} ${formatTime(d)}`;
}

/** 850 → "850 ms"; 3200 → "3,2s"; 72000 → "1m 12s". */
export function formatDuration(ms: Numeric): string {
  const n = toNumber(ms);
  if (n === null || n < 0) return DASH;
  if (n < 1000) return `${formatNumber(Math.round(n))} ms`;
  if (n < 60_000) return `${formatNumber(n / 1000, { maxFractionDigits: 1 })}s`;
  const minutes = Math.floor(n / 60_000);
  const seconds = Math.round((n % 60_000) / 1000);
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/** "2h ago", "just now" — for timestamps in lists. */
export function formatRelative(value: DateInput, now: Date = new Date()): string {
  const d = toDate(value);
  if (!d) return DASH;
  const diff = Math.max(0, now.getTime() - d.getTime());
  const s = Math.floor(diff / 1000);
  if (s < 45) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days}d ago`;
  return formatDate(d);
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

/**
 * Format one table cell of unknown type the way a Vietnamese reader expects.
 * Numeric strings (Postgres NUMERIC arrives as "1284560000.00") are treated as
 * numbers; ISO dates become dd/MM/yyyy (with HH:mm when a time is present).
 */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number' || typeof value === 'bigint') return formatNumber(value);
  if (value instanceof Date) return formatDateTime(value);
  if (typeof value === 'string') {
    const asNumber = toNumber(value);
    if (asNumber !== null && /^[-+]?\d/.test(value.trim())) return formatNumber(asNumber);
    if (ISO_DATE_RE.test(value)) {
      return value.length > 10 ? formatDateTime(value) : formatDate(value);
    }
    return value;
  }
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
}

/** True when a column's values are all numbers / numeric strings (for right-aligning). */
export function isNumericColumn(values: unknown[]): boolean {
  let seen = 0;
  for (const v of values) {
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'number' || typeof v === 'bigint') { seen++; continue; }
    if (typeof v === 'string' && toNumber(v) !== null && /^[-+]?\d/.test(v.trim())) { seen++; continue; }
    return false;
  }
  return seen > 0;
}
