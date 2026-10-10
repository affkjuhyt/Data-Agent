import { StatusDot } from '../../design-system/StatusDot';
import { formatElapsed } from '../../lib/formatVN';
import type { PendingReview } from './api';

/*
 * "Cần bạn quyết định" of "v3.1 · Trang chủ": a card (radius 10, 12×16) with the Heading 2
 * title and the count as a warning dot; each request is a row (8px padding, bottom border)
 * with its question in Body Strong, then "<agent> · <waiting time>" and the action it needs.
 */
const MAX_ROWS = 4;

export function NeedsYouCard({
  reviews,
  error,
  onOpen,
  onRetry,
}: {
  reviews: PendingReview[] | undefined;
  error: boolean;
  onOpen: () => void;
  onRetry: () => void;
}) {
  return (
    <section
      aria-labelledby="needs-you-title"
      className="flex w-full flex-col gap-da-lg rounded-lg border border-border bg-card px-da-xl py-da-lg"
    >
      <div className="flex items-center">
        <h2 id="needs-you-title" className="text-foreground da-heading-2">
          Cần bạn quyết định
        </h2>
        <span className="flex-1" />
        {reviews && reviews.length > 0 && <StatusDot tone="warning">{reviews.length}</StatusDot>}
      </div>

      {error && (
        <p className="text-muted-foreground da-body-small">
          Không tải được việc cần bạn.{' '}
          <button
            type="button"
            onClick={onRetry}
            className="text-primary da-body-strong hover:underline"
          >
            Thử lại
          </button>
        </p>
      )}

      {!error && reviews === undefined && (
        <p className="text-muted-foreground da-body-small">Đang tải…</p>
      )}

      {!error && reviews?.length === 0 && (
        <p className="text-muted-foreground da-body-small">
          Không có việc nào cần bạn. Các agent đang tự làm việc.
        </p>
      )}

      {!error && reviews && reviews.length > 0 && (
        <ul className="flex flex-col">
          {reviews.slice(0, MAX_ROWS).map((review) => (
            <li key={review.conv_id}>
              <button
                type="button"
                onClick={onOpen}
                className="flex w-full flex-col gap-da-sm border-b border-border p-da-md text-left hover:bg-subtle"
              >
                <span className="text-foreground da-body-strong">
                  {review.payload?.title || review.query}
                </span>
                <span className="flex w-full items-center gap-da-md">
                  <span className="min-w-0 flex-1 truncate text-muted-foreground da-caption">
                    {review.agent_id} · {formatElapsed(review.waiting_secs)}
                  </span>
                  <StatusDot tone="warning">Xem yêu cầu</StatusDot>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {reviews && reviews.length > MAX_ROWS && (
        <button
          type="button"
          onClick={onOpen}
          className="self-start text-primary da-body-strong hover:underline"
        >
          Xem tất cả {reviews.length} việc
        </button>
      )}
    </section>
  );
}
