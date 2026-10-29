import { useRef, type ReactNode } from 'react';
import { Dialog as AriaDialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { cn } from '../lib/utils';
import { Icon } from './Icon';
import { useInitialFocus } from './useInitialFocus';

/*
 * A panel from the right edge over the page ("Kết quả chạy thử agent" 2072:4944, Trung tâm thông
 * báo 2072:3821): full height on the scrim, --z-drawer, a header with the title, a caption and ×,
 * a scrolling body and an optional pinned footer. Widths: md 440, receipt 480 ("P01 · Biên nhận câu
 * trả lời" 2420:15098), lg 560. Esc, the × and a click
 * on the scrim close it; focus is trapped like a dialog (React Aria, as in Dialog).
 */
const WIDTH = { md: 'w-[440px]', receipt: 'w-[480px]', lg: 'w-[560px]' } as const;

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
  return (
    <ModalOverlay
      isOpen={open}
      onOpenChange={(next) => !next && onClose()}
      isDismissable
      className="fixed inset-0 z-[var(--z-drawer)] flex justify-end bg-scrim"
    >
      <Modal className={cn('h-full max-w-full outline-none', WIDTH[width])}>
        <DrawerPanel title={title} description={description} onClose={onClose} footer={footer}>
          {children}
        </DrawerPanel>
      </Modal>
    </ModalOverlay>
  );
}

function DrawerPanel({
  title,
  description,
  onClose,
  children,
  footer,
}: {
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useInitialFocus(ref);

  return (
    <AriaDialog className="h-full outline-none">
      <aside ref={ref} className="flex h-full flex-col border-l border-border bg-card shadow-lg">
        <header className="flex items-start gap-da-md border-b border-border px-da-xl py-da-lg">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <Heading slot="title" className="text-foreground da-heading-2">
              {title}
            </Heading>
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
    </AriaDialog>
  );
}
