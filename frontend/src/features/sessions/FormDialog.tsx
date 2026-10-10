import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';

/*
 * A modal form ("Hộp thoại · Chia sẻ phiên" 2297:19093, "Hộp thoại · Lịch chạy" 2239:15782):
 * 560px, card, radius-xl, shadow-lg, 20×24 padding, scrim as ConfirmDialog. ConfirmDialog is
 * for irreversible actions only and has no room for a form, and the design system has no plain
 * dialog yet, so this lives here until one is added (reported).
 * Focus starts on the first field, Tab stays inside, Esc closes.
 */
const FOCUSABLE =
  'button:not([disabled]):not([aria-disabled="true"]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function FormDialog({
  open,
  title,
  children,
  footer,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  footer: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    return () => previous?.focus();
  }, [open]);

  if (!open) return null;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-[rgb(23_33_43/0.32)]"
      onMouseDown={onClose}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="form-dialog-title"
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex max-h-[calc(100vh-64px)] w-[560px] max-w-[calc(100vw-32px)] flex-col gap-[14px] overflow-y-auto rounded-xl bg-card px-da-2xl py-[20px] shadow-lg"
      >
        <h2 id="form-dialog-title" className="text-foreground da-heading-2">
          {title}
        </h2>
        {children}
        <div className="flex items-center gap-da-md">{footer}</div>
      </div>
    </div>
  );
}
