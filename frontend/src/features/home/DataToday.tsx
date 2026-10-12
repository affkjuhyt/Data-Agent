import { Card, CardLink } from '../../design-system/Card';
import { cn } from '../../lib/utils';
import type { Page } from '../../lib/v1/client';
import type { Source } from '../../lib/v1/types';
import type { V1State } from '../../lib/v1/useV1';

/*
 * "Dữ liệu hôm nay" of "v3.1 · Trang chủ": up to four sources, those needing attention first,
 * each a bordered tile (10×12, radius 10) with an 8px status dot, the name, when it last updated
 * and what that means for the reader ("Trễ 8 giờ" in error, "đang đọc" in warning).
 */
const MAX_TILES = 4;

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

function describe(source: Source): { dot: string; line: string; lineTone: string; note: string } {
  if (source.status === 'needs_fix' || (source.late_by_minutes ?? 0) > 0) {
    const hours = Math.round((source.late_by_minutes ?? 0) / 60);
    return {
      dot: 'bg-error',
      line: hours > 0 ? `Trễ ${hours} giờ` : 'Cần sửa kết nối',
      lineTone: 'text-error',
      note: source.status_reason ?? 'Số liệu từ nguồn này có thể cũ',
    };
  }
  if (source.status === 'testing') {
    return {
      dot: 'bg-warning',
      line: source.status_reason ?? 'Đang đọc',
      lineTone: 'text-warning',
      note: 'Đang nạp lần đầu',
    };
  }
  return {
    dot: 'bg-success',
    line: source.last_refreshed_at
      ? `Đã cập nhật ${fmtTime(source.last_refreshed_at)}`
      : 'Đã kết nối',
    lineTone: 'text-muted-foreground',
    note: 'Đúng lịch',
  };
}

export function DataToday({
  sources,
  onManage,
}: {
  sources: V1State<Page<Source>>;
  onManage: () => void;
}) {
  const items = sources.data?.items ?? [];
  if (!sources.data || items.length === 0) return null;
  const attention = (s: Source) => (s.status === 'needs_fix' ? 0 : s.status === 'testing' ? 1 : 2);
  const tiles = [...items].sort((a, b) => attention(a) - attention(b)).slice(0, MAX_TILES);
  // Keep the order of the design: healthy sources first, then the ones to look at.
  tiles.sort((a, b) => attention(b) - attention(a));
  return (
    <Card
      title="Dữ liệu hôm nay"
      action={<CardLink onClick={onManage}>Quản lý kết nối</CardLink>}
      className="px-da-2xl py-[20px]"
    >
      <div className="flex gap-da-lg max-[1023px]:flex-wrap">
        {tiles.map((source) => {
          const d = describe(source);
          return (
            <div
              key={source.id}
              className="flex min-w-0 flex-1 flex-col gap-da-xs rounded-lg border border-border px-da-lg py-[10px]"
            >
              <span className="flex items-center gap-da-sm">
                <span className={cn('size-2 shrink-0 rounded-full', d.dot)} aria-hidden />
                <span className="truncate text-foreground da-body-strong">{source.name}</span>
              </span>
              <span className={cn('da-caption', d.lineTone)}>{d.line}</span>
              <span className="text-muted-foreground da-caption">{d.note}</span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
