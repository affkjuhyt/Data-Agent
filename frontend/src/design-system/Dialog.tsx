import { useId, useRef, type FormEvent, type ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Button } from './Button';
import { Icon } from './Icon';
import { useModalFocus } from './useModalFocus';

/*
 * Figma "Lớp phủ dùng chung · Hộp thoại" (2095:*, e.g. invite 2095:8252, addModel 2095:8757):
 * card on the scrim, radius-xl, shadow-lg, padding 16, title DA/Heading 2 with a caption under it
 * and a × at the right, the body, then the actions right-aligned. Sizes: sm 440 (the
 * ConfirmDialog width), md 560 (a short form), lg 760 (two columns side by side, "Biến phiên
 * thành agent" 2070:3837). For an irreversible action use ConfirmDialog instead.
 *
 * Two ways to fill the footer: pass `onSubmit` + `submitLabel` and the dialog is a form with
 * "Huỷ" and a primary submit (Enter submits); or pass `footer` for anything else.
 */
const WIDTH = { sm: 'w-[440px]', md: 'w-[560px]', lg: 'w-[760px]' } as const;

export function Dialog({
  open,
  title,
  description,
  size = 'md',
  onClose,
  children,
  footer,
  onSubmit,
  submitLabel,
  submitting = false,
  canSubmit = true,
  error,
}: {
  open: boolean;
  title: string;
  description?: ReactNode;
  size?: keyof typeof WIDTH;
  onClose: () => void;
  children: ReactNode;
  /** Custom actions; omit to get "Huỷ" + the submit button. */
  footer?: ReactNode;
  onSubmit?: () => void;
  submitLabel?: string;
  submitting?: boolean;
  canSubmit?: boolean;
  /** The last failure, shown above the actions; the dialog stays open. */
  error?: string;
}) {
  const ref = useRef<HTMLFormElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const onKeyDown = useModalFocus(open, ref, onClose);

  if (!open) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (onSubmit && canSubmit && !submitting) onSubmit();
  };

  const actions =
    footer ??
    (onSubmit ? (
      <>
        <Button kind="ghost" onClick={onClose}>
          Huỷ
        </Button>
        <Button
          kind="primary"
          type="submit"
          loading={submitting}
          disabled={!canSubmit}
        >
          {submitLabel ?? 'Lưu'}
        </Button>
      </>
    ) : null);

  return (
    <div
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-scrim p-da-xl"
      onMouseDown={onClose}
    >
      <form
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        noValidate
        onKeyDown={onKeyDown}
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
        className={cn(
          'flex max-h-[calc(100vh-32px)] max-w-full flex-col gap-da-xl overflow-y-auto rounded-xl bg-card p-da-xl shadow-lg',
          WIDTH[size],
        )}
      >
        <div className="flex items-start gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id={titleId} className="text-foreground da-heading-2">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="text-muted-foreground da-caption">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-foreground hover:bg-subtle"
          >
            <Icon name="x" />
          </button>
        </div>
        {children}
        {error && (
          <p role="alert" className="text-error da-caption">
            {error}
          </p>
        )}
        {actions && <div className="flex justify-end gap-da-md">{actions}</div>}
      </form>
    </div>
  );
}
