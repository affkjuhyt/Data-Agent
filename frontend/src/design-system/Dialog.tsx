import { useId, useRef, type FormEvent, type ReactNode } from 'react';
import { Dialog as AriaDialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { cn } from '../lib/utils';
import { Button } from './Button';
import { Icon } from './Icon';
import { useInitialFocus } from './useInitialFocus';

/*
 * Figma "Lớp phủ dùng chung · Hộp thoại" (2095:*, e.g. invite 2095:8252, addModel 2095:8757):
 * card on the scrim, radius-xl, shadow-lg, padding 16, title DA/Heading 2 with a caption under it
 * and a × at the right, the body, then the actions right-aligned. Sizes: sm 440 (the
 * ConfirmDialog width), md 560 (a short form), lg 760 (two columns side by side, "Biến phiên
 * thành agent" 2070:3837). For an irreversible action use ConfirmDialog instead.
 *
 * Two ways to fill the footer: pass `onSubmit` + `submitLabel` and the dialog is a form with
 * "Huỷ" and a primary submit (Enter submits); or pass `footer` for anything else.
 *
 * React Aria owns the modal behaviour: a portal over the page, Tab trapped, Esc and a click on the
 * scrim close, the page behind hidden from assistive tech, focus back to the opener on close.
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
  return (
    <ModalOverlay
      isOpen={open}
      onOpenChange={(next) => !next && onClose()}
      isDismissable
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-scrim p-da-xl"
    >
      <Modal className={cn('flex max-h-[calc(100vh-32px)] max-w-full outline-none', WIDTH[size])}>
        <DialogPanel
          title={title}
          description={description}
          onClose={onClose}
          footer={footer}
          onSubmit={onSubmit}
          submitLabel={submitLabel}
          submitting={submitting}
          canSubmit={canSubmit}
          error={error}
        >
          {children}
        </DialogPanel>
      </Modal>
    </ModalOverlay>
  );
}

function DialogPanel({
  title,
  description,
  onClose,
  children,
  footer,
  onSubmit,
  submitLabel,
  submitting,
  canSubmit,
  error,
}: {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  onSubmit?: () => void;
  submitLabel?: string;
  submitting: boolean;
  canSubmit: boolean;
  error?: string;
}) {
  const ref = useRef<HTMLFormElement>(null);
  const descriptionId = useId();
  useInitialFocus(ref);

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
        <Button kind="primary" type="submit" loading={submitting} disabled={!canSubmit}>
          {submitLabel ?? 'Lưu'}
        </Button>
      </>
    ) : null);

  return (
    <AriaDialog
      aria-describedby={description ? descriptionId : undefined}
      className="flex min-h-0 w-full rounded-xl bg-card shadow-lg outline-none"
    >
      <form
        ref={ref}
        noValidate
        onSubmit={submit}
        className="flex min-h-0 w-full flex-col gap-da-xl overflow-y-auto p-da-xl"
      >
        <div className="flex items-start gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <Heading slot="title" className="text-foreground da-heading-2">
              {title}
            </Heading>
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
    </AriaDialog>
  );
}
