import { formatNumber, formatPercent, toNumber } from '../../lib/formatVN';

/*
 * The number forms of the admin screens, as the Figma frames write them: "2,1 s" and "180 ms"
 * in the service table, "4,1 giây" in the model table, "2 ph 10 s" for run durations,
 * "4,2 tr" for money in the ops table, "31/12" or "31/03/27" for expiry dates.
 */

/** 2100 → "2,1 s", 180 → "180 ms"; `long` writes "giây" ("4,1 giây"). */
export function formatLatency(ms: number | null, long = false): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${formatNumber(Math.round(ms))} ms`;
  return `${formatNumber(ms / 1000, { maxFractionDigits: 1 })} ${long ? 'giây' : 's'}`;
}

/** 38 → "38 s", 130 → "2 ph 10 s", 65 → "1 ph 05 s". */
export function formatRunDuration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} ph ${String(s % 60).padStart(2, '0')} s`;
}

/** "4200000" → "4,2 tr"; under a million in thousands: "850 ng". */
export function formatMillions(value: string | number): string {
  const n = toNumber(value);
  if (n === null) return '—';
  if (Math.abs(n) < 1e6) return `${formatNumber(n / 1e3, { maxFractionDigits: 0 })} ng`;
  return `${formatNumber(n / 1e6, { maxFractionDigits: 1 })} tr`;
}

/** 18_200_000 → "18,2 triệu"; under a million as is. */
export function formatTokens(value: number | null): string {
  if (value === null) return '—';
  if (value < 1e6) return formatNumber(value);
  return `${formatNumber(value / 1e6, { maxFractionDigits: 1 })} triệu`;
}

/** A ratio as the tables write it: 0.948 → "94,8%", 1 → "100%", 0 → "0%". */
export const pct = (ratio: number | null, digits = 1) =>
  ratio === null ? '—' : formatPercent(ratio, digits);

/** An expiry date: "31/12" this year, "31/03/27" otherwise. */
export function formatExpiry(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = String(d.getDate()).padStart(2, '0');
  const month = String(d.getMonth() + 1).padStart(2, '0');
  if (d.getFullYear() === now.getFullYear()) return `${day}/${month}`;
  return `${day}/${month}/${String(d.getFullYear()).slice(-2)}`;
}

/** "15:00" in the viewer's clock, from an RFC 3339 timestamp. */
export function formatHour(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
