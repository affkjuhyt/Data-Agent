import type { ResolvedRequest } from '../../lib/v1/inbox';
import { whenText } from '../monitoring/whenText';

/*
 * "Cần bạn · lọc Đã xử lý tuần này" (2167:19220): one row per request settled this week — what
 * it was, whether the viewer or one of their rules answered and when, and what followed.
 */
const WEEKDAY = ['Chủ nhật', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy'];

// Today is the time, yesterday "hôm qua", earlier this week the weekday — as the design writes them.
const moment = (iso: string, now: Date) => {
  const text = whenText(iso, now);
  if (text.endsWith('hôm nay')) return text.replace(' hôm nay', '');
  if (text.startsWith('hôm qua')) return 'hôm qua';
  return WEEKDAY[new Date(iso).getDay()];
};

export function ResolvedList({ items }: { items: ResolvedRequest[] }) {
  const now = new Date();
  if (items.length === 0)
    return <p className="text-muted-foreground da-body-small">Tuần này chưa xử lý việc nào.</p>;
  return (
    <ul
      aria-label="Đã xử lý tuần này"
      className="flex flex-col rounded-md border border-border bg-card px-da-xl py-da-sm"
    >
      {items.map((r) => (
        <li
          key={r.id}
          className="flex items-center gap-da-md border-b border-border py-[12px] last:border-b-0"
        >
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="text-foreground da-body-small">{r.title}</span>
            <span className="text-muted-foreground da-caption">
              {r.resolved_by === 'me' ? 'Bạn' : 'Tự xử lý'} · {moment(r.resolved_at, now)}
            </span>
          </span>
          <span className="shrink-0 text-success da-caption">{r.outcome_text}</span>
        </li>
      ))}
    </ul>
  );
}
