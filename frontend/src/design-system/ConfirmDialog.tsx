import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { Button } from './Button';

/*
 * Figma "ConfirmDialog" (2080:712) and its spec: 440px, padding 16, radius-xl, shadow-lg,
 * scrim 32%. Only for actions that cannot be undone; the body states the concrete consequence.
 * Focus starts on "Huỷ", Tab is trapped inside, Esc cancels.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = 'Huỷ',
  tone = 'danger',
  loading = false,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: 'danger' | 'primary';
  loading?: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previous?.focus();
  }, [open]);

  if (!open) return null;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([aria-disabled="true"])',
    );
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
      onMouseDown={onCancel}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-[440px] max-w-[calc(100vw-32px)] flex-col gap-da-md rounded-xl bg-card p-da-xl shadow-lg"
      >
        <h2 id="confirm-dialog-title" className="text-foreground da-heading-2">
          {title}
        </h2>
        <div className="text-muted-foreground da-body-small">{children}</div>
        {error && <p className="text-error da-caption">{error}</p>}
        <div className="flex justify-end gap-da-sm">
          <Button ref={cancelRef} kind="ghost" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button kind={tone} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
