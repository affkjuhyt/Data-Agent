import { useId, useRef, type ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon } from './Icon';
import { useModalFocus } from './useModalFocus';

/*
 * A panel from the right edge over the page ("Kết quả chạy thử agent" 2072:4944, Trung tâm thông
 * báo 2072:3821): full height on the scrim, --z-drawer, a header with the title, a caption and ×,
 * a scrolling body and an optional pinned footer. Widths: md 440, lg 560. Esc, the × and a click
 * on the scrim close it; focus is trapped like a dialog.
 */
const WIDTH = { md: 'w-[440px]', lg: 'w-[560px]' } as const;

export function Drawer({
  open,
  title,
  description,
  width = 'lg',
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  width?: keyof typeof WIDTH;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  const onKeyDown = useModalFocus(open, ref, onClose);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[var(--z-drawer)] flex justify-end bg-scrim" onMouseDown={onClose}>
      <aside
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className={cn('flex h-full max-w-full flex-col border-l border-border bg-card shadow-lg', WIDTH[width])}
      >
        <header className="flex items-start gap-da-md border-b border-border px-da-xl py-da-lg">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id={titleId} className="text-foreground da-heading-2">
              {title}
            </h2>
            {description && <p className="text-muted-foreground da-caption">{description}</p>}
          </div>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-foreground hover:bg-subtle"
          >
            <Icon name="x" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-da-xl py-da-lg">{children}</div>
        {footer && (
          <footer className="flex justify-end gap-da-md border-t border-border px-da-xl py-da-lg">
            {footer}
          </footer>
        )}
      </aside>
    </div>
  );
}
