import { useEffect, useRef, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';

/*
 * "Hộp thoại · invite / memberAdd / createDSR" (2095:8252, 2095:9350, 2095:8857): 560px,
 * padding 16, gap 16, radius-xl, shadow-lg; Heading 2 title with a caption under it and an ×,
 * fields, then Huỷ (ghost) and one primary on the right. Same scrim, Esc and focus trap as
 * ConfirmDialog. The design system has no form dialog yet, so it lives here until it moves there.
 */
export function FormDialog({
  open,
  title,
  description,
  submitLabel,
  loading = false,
  error,
  onSubmit,
  onCancel,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  submitLabel: string;
  loading?: boolean;
  error?: string;
  onSubmit: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>('input, [role="combobox"], button')?.focus();
    return () => previous?.focus();
  }, [open]);

  if (!open) return null;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      // A Select inside handles its own Esc first (closes its list without closing us).
      if (event.defaultPrevented) return;
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'input, textarea, button:not([aria-disabled="true"]), [role="combobox"]',
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

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!loading) onSubmit();
  };

  return (
    <div
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-[rgb(23_33_43/0.32)]"
      onMouseDown={onCancel}
    >
      <form
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="form-dialog-title"
        aria-describedby="form-dialog-description"
        onKeyDown={onKeyDown}
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-[560px] max-w-[calc(100vw-32px)] flex-col gap-da-xl rounded-xl bg-card p-da-xl shadow-lg"
      >
        <div className="flex items-start gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id="form-dialog-title" className="text-foreground da-heading-2">
              {title}
            </h2>
            <p id="form-dialog-description" className="text-muted-foreground da-caption">
              {description}
            </p>
          </div>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onCancel}
            className="rounded-sm text-foreground hover:bg-subtle"
          >
            <Icon name="x" />
          </button>
        </div>
        {children}
        {error && <p className="text-error da-caption">{error}</p>}
        <div className="flex justify-end gap-da-md">
          <Button kind="ghost" onClick={onCancel}>
            Huỷ
          </Button>
          <Button kind="primary" type="submit" loading={loading}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </div>
  );
}
