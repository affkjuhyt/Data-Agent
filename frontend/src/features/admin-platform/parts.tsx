import {
  useEffect,
  useId,
  useRef,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { cn } from '../../lib/utils';

/*
 * Pieces the admin-platform screens share. FormDialog stands in for a design-system form
 * dialog ("Hộp thoại · addModel" 2095:8757, "addIntegration" 2095:8701): the DS only has
 * ConfirmDialog, which takes no fields.
 */

const NOTE_TONE = {
  muted: 'text-muted-foreground',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
} as const;

/** The 14px-padded KPI of "Giám sát hệ thống": caption, 18px value, an 11px coloured note. */
export function MiniKpi({
  label,
  value,
  note,
  noteTone = 'muted',
}: {
  label: string;
  value: string;
  note?: string;
  noteTone?: keyof typeof NOTE_TONE;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-da-xs rounded-md border border-border bg-card p-[14px]">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-metric-small">{value}</span>
      {note && <span className={cn('text-[11px] font-medium', NOTE_TONE[noteTone])}>{note}</span>}
    </div>
  );
}

/** The DA/Metric KPI of "Vận hành agent": caption, 24px value, caption note in its tone. */
export function OpsKpi({
  label,
  value,
  note,
  noteTone = 'muted',
}: {
  label: string;
  value: string;
  note?: string;
  noteTone?: keyof typeof NOTE_TONE;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-da-xs rounded-lg border border-border bg-card p-da-lg">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-metric">{value}</span>
      {note && <span className={cn('da-caption', NOTE_TONE[noteTone])}>{note}</span>}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="font-semibold text-foreground text-[13px] leading-[18px]">{children}</h2>;
}

/** Label / value rows of the settings pages: 220px muted label, value, a bottom rule. */
export function KeyValueList({
  rows,
  mono = false,
}: {
  rows: { key: string; label: ReactNode; value: ReactNode }[];
  mono?: boolean;
}) {
  return (
    <dl className="flex flex-col gap-da-2xl px-da-3xl pt-[20px]">
      {rows.map((row) => (
        <div key={row.key} className="flex items-start gap-da-2xl border-b border-border py-[10px]">
          <dt className="w-[220px] shrink-0 text-muted-foreground da-body-small">{row.label}</dt>
          <dd className={cn('min-w-0 flex-1 text-foreground', mono ? 'da-mono' : 'da-body-small')}>
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/*
 * A modal form: 440px card, radius-xl, shadow-lg, 16px gaps; title + caption, × at the right,
 * the fields, then Huỷ and one primary submit. Esc and the scrim close it; Tab stays inside.
 */
export function FormDialog({
  open,
  title,
  description,
  submitLabel,
  submitting = false,
  canSubmit = true,
  error,
  onSubmit,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  submitLabel: string;
  submitting?: boolean;
  canSubmit?: boolean;
  error?: string;
  onSubmit: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLFormElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input, button[role="combobox"], textarea')?.focus();
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
    const focusable = ref.current?.querySelectorAll<HTMLElement>(
      'input:not([disabled]), button:not([disabled]), textarea:not([disabled])',
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
    if (canSubmit && !submitting) onSubmit();
  };

  return (
    <div
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-[rgb(23_33_43/0.32)]"
      onMouseDown={onClose}
    >
      <form
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-[440px] max-w-[calc(100vw-32px)] flex-col gap-da-xl rounded-xl bg-card p-da-xl shadow-lg"
      >
        <div className="flex items-start gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id={titleId} className="text-foreground da-heading-2">
              {title}
            </h2>
            <p className="text-muted-foreground da-caption">{description}</p>
          </div>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            className="text-foreground hover:text-muted-foreground"
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
        <div className="flex justify-end gap-da-md">
          <Button type="button" kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button type="submit" kind="primary" loading={submitting} disabled={!canSubmit}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </div>
  );
}
