import { formatTime } from '../../lib/formatVN';

/** "07:15 hôm nay", "hôm qua 18:00", otherwise "03/10" — as the P08 lists write a moment. */
export function whenText(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(now) - day(d)) / 86_400_000);
  if (days === 0) return `${formatTime(d)} hôm nay`;
  if (days === 1) return `hôm qua ${formatTime(d)}`;
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}
