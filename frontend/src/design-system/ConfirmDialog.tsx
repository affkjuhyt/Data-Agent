import { useEffect, useRef, type ReactNode } from 'react';
import { Dialog as AriaDialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { cn } from '../lib/utils';
import { Button } from './Button';

/*
 * Figma "ConfirmDialog" (2080:712) and its spec: 440px, padding 16, radius-xl, shadow-lg,
 * scrim 32%. Only for actions that cannot be undone; the body states the concrete consequence.
 * Focus starts on "Huỷ", Tab is trapped inside, Esc cancels (React Aria, as in Dialog).
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
  size = 'sm',
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
  /** sm = 440px of the spec; md = 560px for a dialog that holds a short form. */
  size?: 'sm' | 'md';
}) {
  return (
    <ModalOverlay
      isOpen={open}
      onOpenChange={(next) => !next && onCancel()}
      isDismissable
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-scrim"
    >
      <Modal
        className={cn(
          'max-w-[calc(100vw-32px)] outline-none',
          size === 'md' ? 'w-[560px]' : 'w-[440px]',
        )}
      >
        <AriaDialog
          role="alertdialog"
          className="flex flex-col gap-da-md rounded-xl bg-card p-da-xl shadow-lg outline-none"
        >
          <ConfirmBody
            title={title}
            confirmLabel={confirmLabel}
            cancelLabel={cancelLabel}
            tone={tone}
            loading={loading}
            error={error}
            onConfirm={onConfirm}
            onCancel={onCancel}
          >
            {children}
          </ConfirmBody>
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}

function ConfirmBody({
  title,
  children,
  confirmLabel,
  cancelLabel,
  tone,
  loading,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  tone: 'danger' | 'primary';
  loading: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // The safe choice is the default one; React Aria then finds focus inside and keeps it there.
  useEffect(() => cancelRef.current?.focus(), []);

  return (
    <>
      <Heading slot="title" className="text-foreground da-heading-2">
        {title}
      </Heading>
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
    </>
  );
}
