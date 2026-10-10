import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';

/*
 * Dialog frame of "Xuất kết quả" (2296:19061) and "Báo câu trả lời sai" (2239:15535): 520px,
 * card, radius-xl, shadow-lg, 24px padding, DA/Heading 2 title, scrim as ConfirmDialog.
 * ConfirmDialog only traps focus on buttons and starts on "Huỷ", which leaves the radios and
 * inputs of a form dialog out of keyboard reach, so these dialogs use this frame instead.
 * Focus starts on the first control, Tab stays inside, Esc closes.
 */
const FOCUSABLE =
  'button:not([disabled]):not([aria-disabled="true"]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export function AskDialog({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    // A checked radio is the natural first stop; otherwise the first control.
    const first =
      ref.current?.querySelector<HTMLElement>('input[type="radio"]:checked') ??
      ref.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
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
    const focusable = Array.from(ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
      (el) => !(el instanceof HTMLInputElement && el.type === 'radio' && !el.checked),
    );
    if (focusable.length === 0) return;
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
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex max-h-[calc(100vh-32px)] w-[520px] max-w-[calc(100vw-32px)] flex-col gap-da-lg overflow-y-auto rounded-xl bg-card p-da-2xl shadow-lg"
      >
        <h2 id={titleId} className="m-0 text-foreground da-heading-2">
          {title}
        </h2>
        {children}
        <div className="flex items-center gap-da-md">{footer}</div>
      </div>
    </div>
  );
}
