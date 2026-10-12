import { formatNumber, formatTime } from '../../lib/formatVN';

/** dd/MM, as the P11 tables write expiry dates ("31/12"). */
export function dayMonth(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** "06:00" for a sync time. */
export const clock = (value: string | null | undefined) => (value ? formatTime(value) : '—');

/** Calendar days from `now` to `value`: 0 = today, negative when past. */
export function daysUntil(value: string, now: Date = new Date()): number {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((day(new Date(value)) - day(now)) / 86_400_000);
}

/** YYYY-MM-DD in local time, for date-only fields. */
export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function addDays(days: number, from: Date = new Date()): Date {
  const d = new Date(from);
  d.setDate(d.getDate() + days);
  return d;
}

export const count = (n: number) => formatNumber(n);

/** Row scopes the dialogs offer; null = the whole bank. PROPOSAL: from the identity attributes. */
export const ROW_SCOPES: { value: string; label: string }[] = [
  { value: 'all', label: 'Toàn hàng' },
  { value: 'Theo vùng phụ trách', label: 'Theo vùng phụ trách' },
  { value: 'Vùng: Bắc', label: 'Vùng: Bắc' },
  { value: 'Vùng: Nam', label: 'Vùng: Nam' },
  { value: 'Chi nhánh của mình', label: 'Chi nhánh của mình' },
];
export const scopeValue = (value: string) => (value === 'all' ? null : value);

export const EXPIRIES: { value: string; label: string }[] = [
  { value: 'none', label: 'Không' },
  { value: '30', label: '30 ngày' },
  { value: '90', label: '90 ngày' },
];
export const expiryValue = (value: string) =>
  value === 'none' ? null : isoDay(addDays(Number(value)));
