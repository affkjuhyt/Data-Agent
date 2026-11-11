import type { ReactNode } from 'react';
import { ErrorBoundary, type FallbackProps } from 'react-error-boundary';
import { Button } from './Button';
import { EmptyState } from './EmptyState';
import { Notice } from './Notice';

/*
 * A render error used to blank the whole app: nothing caught it, so React unmounted the tree from
 * the root. PageBoundary keeps the shell (top bar, rail) and shows the page's failure with a retry;
 * it starts over when the page changes (resetKeys). BlockBoundary does the same for one block of
 * an answer, so a chart or a table that cannot be drawn leaves the rest of the answer readable.
 * Data errors (a 4xx/5xx) are AsyncRegion's to show; these catch what rendering itself throws.
 */

function PageFallback({ resetErrorBoundary }: FallbackProps) {
  return (
    <div className="px-da-3xl py-da-2xl">
      <EmptyState
        icon="alert"
        title="Màn hình này gặp lỗi khi hiển thị"
        description="Dữ liệu vẫn an toàn. Thử lại, hoặc chọn màn hình khác ở thanh bên."
        action={<Button onClick={resetErrorBoundary}>Thử lại</Button>}
      />
    </div>
  );
}

export function PageBoundary({
  resetKeys,
  children,
}: {
  /** What identifies the page shown (area, page, id): a new page starts with no error. */
  resetKeys: unknown[];
  children: ReactNode;
}) {
  return (
    <ErrorBoundary FallbackComponent={PageFallback} resetKeys={resetKeys}>
      {children}
    </ErrorBoundary>
  );
}

function BlockFallback() {
  return <Notice tone="warning">Không hiển thị được phần này của câu trả lời.</Notice>;
}

export function BlockBoundary({ children }: { children: ReactNode }) {
  return <ErrorBoundary FallbackComponent={BlockFallback}>{children}</ErrorBoundary>;
}
