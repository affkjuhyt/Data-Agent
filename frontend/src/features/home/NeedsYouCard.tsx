import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Card } from '../../design-system/Card';
import { StatusDot } from '../../design-system/StatusDot';
import { formatElapsed } from '../../lib/formatVN';
import type { Page } from '../../lib/v1/client';
import type { HitlRequest } from '../../lib/v1/types';
import type { V1State } from '../../lib/v1/useV1';
import { DECISION, decisionKindOf, detailRouteOf } from '../needs-you/decision';

/*
 * "Cần bạn quyết định" of "v3.1 · Trang chủ": the open requests (GET /v1/inbox), each a row of
 * 8px padding with a bottom border: the question in Body Strong, then "<agent> · <waiting>" and
 * what it needs, as a dot in the kind's colour. The count sits at the right of the title.
 */
const MAX_ROWS = 4;

const ACTION = {
  choose: '',
  approve: 'Xem thay đổi',
  compare: 'So sánh nguồn',
  review: 'Mở báo cáo',
} as const;

export function NeedsYouCard({
  inbox,
  onOpen,
  onOpenDetail,
}: {
  inbox: V1State<Page<HitlRequest>>;
  onOpen: () => void;
  /** A row with a detail page opens it; the others open Cần bạn. */
  onOpenDetail: (page: string, id: string) => void;
}) {
  const items = inbox.data?.items ?? [];
  return (
    <Card
      title="Cần bạn quyết định"
      action={items.length > 0 ? <StatusDot tone="warning">{items.length}</StatusDot> : undefined}
    >
      <AsyncRegion
        state={inbox}
        what="việc cần bạn"
        isEmpty={(page) => page.items.length === 0}
        empty={
          <p className="text-muted-foreground da-body-small">
            Không có việc nào cần bạn. Các agent đang tự làm việc.
          </p>
        }
      >
        {(page) => (
          <ul className="flex flex-col">
            {page.items.slice(0, MAX_ROWS).map((request) => {
              const kind = decisionKindOf(request);
              const look = DECISION[kind];
              const waited = (Date.now() - new Date(request.created_at).getTime()) / 1000;
              return (
                <li key={request.id}>
                  <button
                    type="button"
                    onClick={() => {
                      const detail = detailRouteOf(request);
                      if (detail) onOpenDetail(detail.page, detail.id);
                      else onOpen();
                    }}
                    className="flex w-full flex-col gap-da-sm border-b border-border p-da-md text-left hover:bg-subtle"
                  >
                    <span className="text-foreground da-body-strong">{request.title}</span>
                    <span className="flex w-full items-center gap-da-md">
                      <span className="min-w-0 flex-1 truncate text-muted-foreground da-caption">
                        {[request.agent?.name, formatElapsed(waited)].filter(Boolean).join(' · ')}
                      </span>
                      <StatusDot tone={look.dot}>{ACTION[kind] || look.label(request)}</StatusDot>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </AsyncRegion>
      {items.length > MAX_ROWS && (
        <button
          type="button"
          onClick={onOpen}
          className="self-start text-primary da-body-strong hover:underline"
        >
          Xem tất cả {items.length} việc
        </button>
      )}
    </Card>
  );
}
