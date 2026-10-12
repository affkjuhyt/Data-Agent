import type { ReactNode } from 'react';
import { cn } from '../lib/utils';

/*
 * The bordered block the v3.1 screens are built from ("Dashboard", "Phiên gần đây",
 * "Thay đổi gần đây"…): card background, border, radius 10, 12×16 padding, 8px gap.
 * The title is DA/Heading 2 (or Heading 3 with `size="sm"`); `action` sits at the right of it.
 */
export function Card({
  title,
  size = 'md',
  action,
  children,
  className,
}: {
  title?: ReactNode;
  size?: 'md' | 'sm';
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        'flex min-w-0 flex-col gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg',
        className,
      )}
    >
      {(title || action) && (
        <div className="flex items-center gap-da-md">
          {title && (
            <h2
              className={cn(
                'min-w-0 flex-1 text-foreground',
                size === 'md' ? 'da-heading-2' : 'da-heading-3',
              )}
            >
              {title}
            </h2>
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/** "Xem tất cả", "Quản lý kết nối →": the text link at the right of a card title. */
export function CardLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 text-primary da-body-strong hover:underline"
    >
      {children}
    </button>
  );
}
