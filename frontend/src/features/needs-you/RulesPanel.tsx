import type { BehaviorPolicy } from '../../lib/v1/types';
import type { V1State } from '../../lib/v1/useV1';
import type { Page } from '../../lib/v1/client';

/*
 * Right panel of "v3.1 · Cần bạn" (2149:15939): 380px, left border, 16px padding, 14px gap.
 * How much the viewer's rules handled this week (DA/Metric in success), the rules with their
 * use count, and when agents ask at all. Without rules only the explanation shows.
 */
export function RulesPanel({ rules }: { rules: V1State<Page<BehaviorPolicy>> }) {
  const items = rules.data?.items ?? [];
  const handled = items.reduce((sum, rule) => sum + rule.applied_this_week, 0);

  return (
    <aside
      aria-label="Quy tắc của bạn"
      className="flex w-[380px] shrink-0 flex-col gap-[14px] border-l border-border bg-card p-da-xl max-[1279px]:hidden"
    >
      {items.length > 0 && (
        <>
          <div className="flex flex-col gap-da-xs rounded-lg border border-border px-da-lg py-da-md">
            <span className="text-success da-metric">{handled} việc</span>
            <span className="text-foreground da-body-small">
              đã được tự xử lý tuần này nhờ {items.length} quy tắc bạn tạo từ quyết định trước đây.
            </span>
          </div>
          <h2 className="text-foreground da-heading-3">Quy tắc của bạn</h2>
          <ul className="flex flex-col gap-[14px]">
            {items.map((rule) => (
              <li
                key={rule.id}
                className="flex items-start gap-da-md rounded-md border border-border px-da-md py-da-sm"
              >
                <span className="min-w-0 flex-1 text-foreground da-body-small">
                  {rule.description}
                </span>
                <span className="shrink-0 text-muted-foreground da-caption">
                  {rule.applied_this_week} lần
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="flex flex-col gap-da-xs rounded-md border border-border p-da-md">
        <span className="text-muted-foreground da-label">Agent hỏi bạn khi nào?</span>
        <span className="text-foreground da-caption">
          Khi định nghĩa mơ hồ, khi cần sửa số liệu, khi hai nguồn mâu thuẫn, hoặc trước khi gửi kết
          quả ra ngoài. Bạn cũng nhận các việc này qua Teams.
        </span>
      </div>
    </aside>
  );
}
